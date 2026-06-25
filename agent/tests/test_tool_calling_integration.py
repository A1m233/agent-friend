"""005 集成测：工具调用循环 happy path / 失败兜底 / 触上限。

覆盖 docs/requirements/005-engine-tool-calling-and-web-search/design.md §2.1
（M5.1 端到端单测覆盖）的三大场景：

- :func:`test_tool_call_happy_path` — LLM 触发 → 执行 → 喂回 → AI 整合回复
- :func:`test_tool_failure_does_not_break_loop` — tool 抛异常时循环不中断
- :func:`test_tool_loop_max_turns_triggers_finalization` — LLM 死循环达上限触发兜底
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
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
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    ToolRegistry,
    ToolResult,
    TurnDone,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)

# ---- Fake tools ----


class EchoTool:
    """简单回声 tool：原样返回输入文本。"""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "回声工具：原样返回输入文本，便于测试。"
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


class FailingTool:
    """总是抛异常的 tool。"""

    name: ClassVar[str] = "fail_tool"
    description: ClassVar[str] = "总是失败的工具，测试错误兜底路径。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
    }

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        raise RuntimeError("simulated tool crash")


class SpyRecallTool:
    """记录 ``recall_past_chats`` 的真实 invoke 入参，验证隐藏上下文注入。"""

    name: ClassVar[str] = "recall_past_chats"
    description: ClassVar[str] = "回忆过去和这位用户聊过的事。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
    }

    def __init__(self) -> None:
        self.seen_args: list[dict[str, Any]] = []

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.seen_args.append(dict(args))
        return ToolResult(text="找到一些旧聊天。")


# ---- Scripted fake LLM ----


@dataclass
class _ScriptedLLMClient:
    """按脚本（每轮一个 LLMStreamEvent 列表）依次回放 stream 输出。

    每次调 :meth:`stream` 消费 ``self.script`` 列表里的下一项。``self.received``
    保留每轮收到的 messages 用于断言。
    """

    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000  # 009：Conversation 经此推导预算阈值

    def complete(
        self, messages: list[dict[str, Any]], **overrides: Any
    ) -> str:  # pragma: no cover - send 路径不测
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
    registry: ToolRegistry | None = None,
) -> Conversation:
    """组装最小可跑的 :class:`Conversation`：fake LLM + 真 catalog + 内存内 store。"""
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
    )


# ===== happy path =====


def test_tool_call_happy_path(tmp_path: Path) -> None:
    """LLM 第一轮调 echo tool → 工具执行 → LLM 第二轮整合结果输出 → 结束。"""
    echo = EchoTool()
    registry = ToolRegistry([echo])

    fake = _ScriptedLLMClient(
        script=[
            # turn 0: LLM 要求调 echo
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_1",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "hi"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            # turn 1: LLM 收到 tool 结果，整合输出
            [
                LLMTextDelta(text="基于 echo 结果："),
                LLMTextDelta(text="echo: hi"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("hi"))

    text_deltas = [ev for ev in events if isinstance(ev, TextDelta)]
    tool_reqs = [ev for ev in events if isinstance(ev, ToolCallRequest)]
    tool_results = [ev for ev in events if isinstance(ev, ToolCallResult)]
    dones = [ev for ev in events if isinstance(ev, TurnDone)]

    assert len(tool_reqs) == 1
    assert tool_reqs[0].tool_name == "echo"
    assert tool_reqs[0].tool_call_id == "call_1"
    assert tool_reqs[0].args == {"message": "hi"}

    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    assert tool_results[0].text == "echo: hi"
    assert tool_results[0].tool_call_id == "call_1"

    assert "".join(t.text for t in text_deltas) == "基于 echo 结果：echo: hi"

    assert len(dones) == 1
    assert dones[0].stop_reason == "end_turn"
    assert dones[0].total_tool_calls == 1

    assert echo.call_count == 1

    # 事件落盘：session_meta + user + assistant(turn 0) + tool_call_request +
    # tool_call_result + assistant(turn 1)
    types = [ev.type for ev in conv.session.events]
    assert types[0] == "session_meta"
    assert types.count("user_message") == 1
    assert types.count("assistant_message") == 2
    assert types.count("tool_call_request") == 1
    assert types.count("tool_call_result") == 1

    # 第二轮 messages 应包含 tool message（让 LLM 看到结果）
    second_turn_messages = fake.received[1]
    tool_role_msgs = [m for m in second_turn_messages if m.get("role") == "tool"]
    assert len(tool_role_msgs) == 1
    assert tool_role_msgs[0]["content"] == "echo: hi"
    assert tool_role_msgs[0]["tool_call_id"] == "call_1"

    # 第二轮 messages 中 assistant 消息应携带 tool_calls 字段
    assistant_msgs = [m for m in second_turn_messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert "tool_calls" in assistant_msgs[0]
    assert assistant_msgs[0]["tool_calls"][0]["id"] == "call_1"


def test_recall_tool_receives_hidden_current_turn_context(tmp_path: Path) -> None:
    """``recall_past_chats`` 执行时拿到当前 session / turn 起点，落盘参数仍保持原样。"""
    recall = SpyRecallTool()
    registry = ToolRegistry([recall])
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="让我回忆一下。"),
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_recall",
                    tool_name="recall_past_chats",
                    args_json_delta=json.dumps({"limit": 10}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMTextDelta(text="想起来了。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("我们最近聊了啥？"))

    assert len(recall.seen_args) == 1
    invocation_args = recall.seen_args[0]
    assert invocation_args["limit"] == 10
    assert invocation_args["__agent_friend_current_session_id"] == conv.session.session_id
    assert invocation_args["__agent_friend_current_turn_start_index"] == 1

    tool_reqs = [ev for ev in events if isinstance(ev, ToolCallRequest)]
    assert len(tool_reqs) == 1
    assert tool_reqs[0].args == {"limit": 10}

    request_event = next(ev for ev in conv.session.events if ev.type == "tool_call_request")
    assert request_event.payload["args"] == {"limit": 10}


def test_closing_stream_after_turndone_does_not_write_partial_duplicate(tmp_path: Path) -> None:
    """消费方拿到 TurnDone 后关闭 generator，不应误落一条 partial=True 重复回复。"""
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="完整回复"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([]))

    stream = cast(Generator[Any, None, None], conv.stream("hi"))
    first = next(stream)
    done = next(stream)
    assert isinstance(first, TextDelta)
    assert isinstance(done, TurnDone)

    stream.close()

    assistant_events = [ev for ev in conv.session.events if ev.type == "assistant_message"]
    assert len(assistant_events) == 1
    assert assistant_events[0].payload["content"] == "完整回复"
    assert assistant_events[0].payload["partial"] is False


# ===== 失败兜底 =====


def test_tool_failure_does_not_break_loop(tmp_path: Path) -> None:
    """工具抛异常 → 转 ToolResult(is_error=True) → 循环不中断 → 第二轮整合输出后结束。"""
    fail = FailingTool()
    registry = ToolRegistry([fail])
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_x",
                    tool_name="fail_tool",
                    args_json_delta="{}",
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMTextDelta(text="抱歉，刚才碰到点问题。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("call fail"))
    tool_results = [ev for ev in events if isinstance(ev, ToolCallResult)]
    dones = [ev for ev in events if isinstance(ev, TurnDone)]

    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "simulated tool crash" in tool_results[0].text

    text = "".join(ev.text for ev in events if isinstance(ev, TextDelta))
    assert "抱歉" in text

    assert len(dones) == 1
    assert dones[0].stop_reason == "end_turn"


# ===== 触上限 =====


def test_tool_loop_max_turns_triggers_finalization(tmp_path: Path) -> None:
    """LLM 持续 stop_reason=tool_use 直至循环上限 → 触发兜底，输出收尾文本。

    默认 ``MAX_TOOL_TURNS_DEFAULT = 5``：循环 ``range(6) = 0..5``；前 5 轮各
    执行一次 tool（turn 0..4），turn 5 LLM 完成后 ``turn_idx >= 5`` break，
    不再执行 tool；进入兜底再调一次 LLM（``tools=None``）输出收尾文本。
    """
    echo = EchoTool()
    registry = ToolRegistry([echo])

    def tool_use_events(call_id: str) -> list[LLMStreamEvent]:
        return [
            LLMToolCallDelta(
                index=0,
                tool_call_id=call_id,
                tool_name="echo",
                args_json_delta=json.dumps({"message": "loop"}),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ]

    fake = _ScriptedLLMClient(
        script=[tool_use_events(f"call_{i}") for i in range(6)]
        + [
            [
                LLMTextDelta(text="我尽力了，没找到更好的回答。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("loop forever"))
    dones = [ev for ev in events if isinstance(ev, TurnDone)]

    assert len(dones) == 1
    assert dones[0].stop_reason == "max_turns_reached"
    assert dones[0].total_tool_calls == 5

    text = "".join(ev.text for ev in events if isinstance(ev, TextDelta))
    assert "尽力了" in text

    assert echo.call_count == 5

    # 兜底调用（最后一次 LLM 调用）messages 应包含 "不要再调用任何工具" 的 system msg
    finalization_messages = fake.received[-1]
    system_msgs = [m for m in finalization_messages if m.get("role") == "system"]
    assert any("不要再调用任何工具" in m["content"] for m in system_msgs)
