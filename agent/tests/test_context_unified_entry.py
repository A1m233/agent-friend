"""009 M1：统一 LLM 上下文入口（R-0.3）+ usage 消费 + 度量可观测 集成测。

覆盖：

- 首轮 / 工具续轮 / 触上限兜底收尾 **三入口都经过** ``ContextManager``
- 续轮 ``new_user_input=None``、兜底带 ``trailing_system``、均传 ``runtime``
- ``LLMTurnDone.usage`` 被消费为下一轮估算锚点
- ``Conversation.last_context_notes`` 暴露 token 估算 / 窗口 / 阈值
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

from agent.context import assemble_messages
from agent.messages import Message

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    BuildResult,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    PromptBuilder,
    RuntimeContext,
    Session,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
)

# ---- 间谍 context manager：记录每次 build_messages 的调用形态 ----


@dataclass
class _SpyContextManager:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        extra_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult:
        self.calls.append(
            {
                "new_user_input": new_user_input,
                "trailing_user": trailing_user,
                "trailing_system": trailing_system,
                "has_runtime": runtime is not None,
            }
        )
        msgs = assemble_messages(
            history=history,
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            extra_context=extra_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )
        return BuildResult(messages=msgs)


# ---- 脚本化 fake LLM（带 usage）----


@dataclass
class _UsageLLM:
    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    context_window: int = 100_000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


class _LoopTool:
    name: ClassVar[str] = "loop"
    description: ClassVar[str] = "总是被调用，制造工具循环。"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(text="looped")


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: Any,
    context_manager: Any,
    registry: ToolRegistry | None = None,
) -> Conversation:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t", persona="default", model="deepseek/deepseek-chat", persona_id=persona_id
    )
    store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=cast(Any, fake_llm),
        context_manager=cast(Any, context_manager),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=registry,
    )


def _tool_use_turn() -> list[LLMStreamEvent]:
    return [
        LLMToolCallDelta(index=0, tool_call_id="c", tool_name="loop", args_json_delta="{}"),
        LLMTurnDone(stop_reason="tool_use"),
    ]


def test_all_three_entries_go_through_context_manager(tmp_path: Path) -> None:
    """首轮 + 续轮 + 兜底收尾都经过 ContextManager（R-0.3）。"""
    spy = _SpyContextManager()
    # LLM 一直要求调工具 → 跑满 max_tool_turns → 触发兜底收尾
    fake = _UsageLLM(script=[_tool_use_turn() for _ in range(10)])
    conv = _make_conversation(
        tmp_path, fake_llm=fake, context_manager=spy, registry=ToolRegistry([_LoopTool()])
    )

    list(conv.stream("hi"))

    # 至少 3 次调用：首轮 + 若干续轮 + 兜底收尾
    assert len(spy.calls) >= 3

    # 首轮：带 new_user_input，无 trailing
    assert spy.calls[0]["new_user_input"] == "hi"
    assert spy.calls[0]["trailing_system"] is None

    # 中间续轮：无 new_user_input、无 trailing
    middle = spy.calls[1:-1]
    assert middle, "应至少有一次工具续轮"
    assert all(c["new_user_input"] is None for c in middle)
    assert all(c["trailing_system"] is None for c in middle)

    # 兜底收尾（最后一次）：无 new_user_input、带 trailing_system
    last = spy.calls[-1]
    assert last["new_user_input"] is None
    assert last["trailing_system"] is not None

    # 所有入口都传了 runtime
    assert all(c["has_runtime"] for c in spy.calls)


def test_usage_consumed_as_next_anchor_and_observable(tmp_path: Path) -> None:
    """LLMTurnDone.usage → 下一轮估算锚点；last_context_notes 暴露度量。"""
    spy = _SpyContextManager()
    fake = _UsageLLM(
        script=[
            [
                LLMTextDelta(text="hi"),
                LLMTurnDone(
                    stop_reason="end_turn",
                    usage=LLMUsage(prompt_tokens=1234, completion_tokens=5, total_tokens=1239),
                ),
            ],
            [LLMTextDelta(text="yo"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, context_manager=spy)

    conv.send("hi")
    # 首轮锚点为 None
    notes1 = conv.last_context_notes
    assert notes1["last_input_tokens"] is None
    assert notes1["effective_window"] == 100_000
    assert notes1["trigger_threshold"] == 80_000
    assert "token_estimate" in notes1

    conv.send("yo")
    # 第二轮锚点应为首轮 usage.prompt_tokens
    assert conv.last_context_notes["last_input_tokens"] == 1234
