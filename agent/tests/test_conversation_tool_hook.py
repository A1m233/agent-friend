"""014 单测：``Conversation`` 的 ``tool_hook_invoker`` 注入路径。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §3.4
的 tool 边界 hook 注入机制：

- 不注入（``tool_hook_invoker=None``）→ ``_invoke_tool_safely`` 走原行为
  （由 ``tests/test_tool_calling_integration.py`` 覆盖；本文件不重复）
- 注入 PROCEED 形态的 invoker → 调 default_invoke 拿真 result + 可旁路观察
- 注入 BLOCK 形态的 invoker → 不调 default_invoke、返回业务级失败 ToolResult，
  循环把它当 tool_call_result 喂回 LLM（不打断主流程）

对应 requirement.md AC-3（PreToolUse 短路）维度最小验证。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)


class _EchoTool:
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "回声工具：原样返回输入文本。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def __init__(self) -> None:
        self.call_count = 0

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.call_count += 1
        return ToolResult(text=f"echo: {args['message']}")


@dataclass
class _ScriptedLLMClient:
    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:  # pragma: no cover
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(messages)
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: _ScriptedLLMClient,
    registry: ToolRegistry,
    tool_hook_invoker: Callable[[str, dict[str, Any], Callable[[], ToolResult]], ToolResult]
    | None = None,
) -> Conversation:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=persona_id,
    )
    store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, fake_llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=registry,
        tool_hook_invoker=tool_hook_invoker,
    )


def _two_turn_script_invoking_echo() -> list[list[LLMStreamEvent]]:
    """LLM 第 1 轮要求调 echo({"message": "hi"}) → 第 2 轮整合 tool 结果输出。"""
    return [
        [
            LLMToolCallDelta(
                index=0,
                tool_call_id="call_1",
                tool_name="echo",
                args_json_delta=json.dumps({"message": "hi"}),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ],
        [
            LLMTextDelta(text="done"),
            LLMTurnDone(stop_reason="end_turn"),
        ],
    ]


# ===== PROCEED：invoker 调 default_invoke =====


def test_tool_hook_invoker_proceed_calls_default_and_observes(tmp_path: Path) -> None:
    """注入 invoker 后 PROCEED 形态 = 调 default_invoke 拿真 result；可旁路记录调用。"""
    echo = _EchoTool()
    registry = ToolRegistry([echo])

    observed: list[tuple[str, dict[str, Any], ToolResult]] = []

    def proceed_invoker(
        name: str, args: dict[str, Any], default_invoke: Callable[[], ToolResult]
    ) -> ToolResult:
        result = default_invoke()
        observed.append((name, args, result))
        return result

    fake = _ScriptedLLMClient(script=_two_turn_script_invoking_echo())
    conv = _make_conversation(
        tmp_path, fake_llm=fake, registry=registry, tool_hook_invoker=proceed_invoker
    )

    list(conv.stream("hi"))

    # tool 实际被调用了一次
    assert echo.call_count == 1
    # invoker 旁路看到了 name / args / 真 result
    assert len(observed) == 1
    name, args, result = observed[0]
    assert name == "echo"
    assert args == {"message": "hi"}
    assert result.text == "echo: hi"
    assert result.is_error is False


# ===== BLOCK：invoker 不调 default_invoke，返回业务级失败 =====


def test_tool_hook_invoker_block_short_circuits_without_calling_tool(tmp_path: Path) -> None:
    """注入 invoker 后 BLOCK 形态 = **不**调 default_invoke、返回 is_error=True；
    LLM 收到 tool_call_result 后正常进入下一轮整合输出（循环不中断）。"""
    echo = _EchoTool()
    registry = ToolRegistry([echo])

    def block_invoker(
        name: str, args: dict[str, Any], default_invoke: Callable[[], ToolResult]
    ) -> ToolResult:
        # 模拟 PreToolUse hook 返回 BLOCK(reason)
        return ToolResult(text=f"工具 {name!r} 被拒：模拟权限策略", is_error=True)

    fake = _ScriptedLLMClient(script=_two_turn_script_invoking_echo())
    conv = _make_conversation(
        tmp_path, fake_llm=fake, registry=registry, tool_hook_invoker=block_invoker
    )

    list(conv.stream("hi"))

    # 真 tool 完全没被调
    assert echo.call_count == 0

    # session 里仍落了 tool_call_request + tool_call_result（BLOCK 不打断主流程）
    types = [ev.type for ev in conv.session.events]
    assert types.count("tool_call_request") == 1
    assert types.count("tool_call_result") == 1
    # tool_call_result 的 content 是 invoker 返回的 BLOCK 文案 + is_error=True
    result_event = next(ev for ev in conv.session.events if ev.type == "tool_call_result")
    assert result_event.payload["is_error"] is True
    assert "被拒" in result_event.payload["content"]


# ===== 兼容性：未注入 invoker 时行为完全不变 =====


def test_tool_hook_invoker_none_preserves_default_behavior(tmp_path: Path) -> None:
    """未注入 invoker（None）时，_invoke_tool_safely 走原行为——直接调 registry.invoke。"""
    echo = _EchoTool()
    registry = ToolRegistry([echo])

    fake = _ScriptedLLMClient(script=_two_turn_script_invoking_echo())
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry, tool_hook_invoker=None)

    list(conv.stream("hi"))

    assert echo.call_count == 1
    types = [ev.type for ev in conv.session.events]
    assert "tool_call_request" in types
    assert "tool_call_result" in types
