"""014 单测：``AgentRuntime`` dispatch 主流程（UserEvent + SystemTriggerEvent user）。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §3.3 / §8.3；
对应 requirement.md AC-1（main loop dispatch 可跑通） + AC-5（BedtimeSource demo
端到端，runtime 这一层）。

不覆盖 silent turn（见 test_runtime_silent_turn.py）与 _observe_turn 迁移
（见 test_runtime_observe_migration.py）。

测试策略：
- ``_dispatch`` 同步驱动（不起 thread），避免计时 flakiness
- 起一个侧 thread 跑 asyncio loop 接 Subscriber，验证跨 thread fan-out + envelope
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from agent.runtime import (
    AgentRuntime,
    PostTurnContext,
    PushEnvelope,
    RunOutcome,
    Subscriber,
    SystemTriggerEvent,
    UserEvent,
)
from agent.sessions.changes import SessionChange
from agent.sessions.tool_execution_evidence import (
    ToolExecutionEvidence,
    ToolExecutionEvidenceStore,
)
from agent.system_prompt import IM_QQ_SURFACE
from agent.tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    ConversationEvent,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    Message,
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
    LanguageModel,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
)


@dataclass
class _ScriptedLLMClient:
    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


class _CancelAwareBlockingLanguageModel:
    context_window = 128000

    def __init__(self) -> None:
        self.request_started = threading.Event()

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        cancel_check = request.control.cancel_check
        self.request_started.set()
        assert callable(cancel_check)
        while True:
            cancel_check()
            time.sleep(0.01)
        yield  # pragma: no cover - keeps this method an iterator


class _BlockingTool:
    name: ClassVar[str] = "blocking_tool"
    description: ClassVar[str] = "Block until the test releases the invocation."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()
        self.invocations = 0

    def invoke(self, _args: dict[str, Any]) -> ToolResult:
        self.invocations += 1
        self.started.set()
        self.release.wait(timeout=2)
        self.completed.set()
        return ToolResult(text="late result")


class _BlockingCommandEvidenceTool:
    name: ClassVar[str] = "exec_command"
    description: ClassVar[str] = "Emit persisted partial output and block until cancelled."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    supports_invocation_control: ClassVar[bool] = True

    def __init__(self, evidence_store: ToolExecutionEvidenceStore) -> None:
        self._evidence_store = evidence_store
        self.started = threading.Event()
        self.release = threading.Event()
        self.invocations = 0

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        from agent.command_runtime.tools import (
            COMMAND_EXECUTION_CHECKPOINT_ARG,
            COMMAND_RUN_ID_ARG,
            COMMAND_SESSION_ID_ARG,
            COMMAND_TOOL_CALL_ID_ARG,
        )

        self.invocations += 1
        evidence_ref = self._evidence_store.new_reference()
        checkpoint = args[COMMAND_EXECUTION_CHECKPOINT_ARG]
        checkpoint("proc_evidence", "resident_only", evidence_ref)
        self._evidence_store.write(
            ToolExecutionEvidence(
                evidence_ref=evidence_ref,
                session_id=str(args[COMMAND_SESSION_ID_ARG]),
                run_id=str(args[COMMAND_RUN_ID_ARG]),
                tool_call_id=str(args[COMMAND_TOOL_CALL_ID_ARG]),
                operation_id="proc_evidence",
                tool_name=self.name,
                process_status="running",
                output_seq=4,
                dropped_bytes=7,
                stdout="persisted partial stdout",
                stderr="persisted partial stderr",
                captured_at=datetime.now(UTC).isoformat(),
            )
        )
        control = args.get(TOOL_INVOCATION_CONTROL_ARG)
        if isinstance(control, ToolInvocationControl):
            control.add_cancel_callback(lambda _reason: self.release.set())
        self.started.set()
        self.release.wait(timeout=2)
        return ToolResult(
            text="late command result",
            meta={"_execution_evidence_ref": evidence_ref},
        )


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: LanguageModel,
    tool_registry: ToolRegistry | None = None,
    tool_execution_evidence_store: ToolExecutionEvidenceStore | None = None,
) -> Conversation:
    """与 AgentRuntime 装配的 Conversation 一致：post_turn_external=True，
    避免 finally 块重复跑 _observe_turn。"""
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
        llm_client=fake_llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=tool_registry,
        tool_execution_evidence_store=tool_execution_evidence_store,
        post_turn_external=True,
    )


@contextmanager
def _side_event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """起一个侧 thread 跑 asyncio loop，供 Subscriber 的 queue 接收 envelope。

    用 contextmanager 确保 loop / thread 一定关闭。
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="SideLoop")
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        loop.close()


def _await_envelope(sub: Subscriber, *, timeout: float = 2.0) -> PushEnvelope:
    """从侧 loop 上的订阅者 queue 拿一个 envelope（跨 thread）。"""
    fut = asyncio.run_coroutine_threadsafe(sub.queue.get(), sub.loop)
    return fut.result(timeout=timeout)


# ===== UserEvent dispatch（AC-1 + R-4.2.2） =====


def test_dispatch_user_event_runs_stream_and_falls_to_listener(tmp_path: Path) -> None:
    """UserEvent → conv.stream 被调 → 每个 ConversationEvent fan-out 给 listener →
    TurnDone 时打包推送一个 envelope（kind=user_turn，含 events 列表）。"""
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="hi "),
                LLMTextDelta(text="there"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    runtime = AgentRuntime(conversation_factory=lambda sid: conv)

    with _side_event_loop() as loop:
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"user_turn", "agent_turn"}))
        runtime.listeners.register(sub)

        runtime._dispatch(UserEvent(session_id=conv.session.session_id, user_input="hello"))

        env = _await_envelope(sub)
        assert env.kind == "user_turn"
        assert env.session_id == conv.session.session_id
        assert env.source_kind is None
        assert env.seq == 1
        # events 内含初始等待状态 + TextDelta * 2 + TurnDone
        types = [e["type"] for e in env.events]
        assert types == [
            "model_response_status",
            "assistant_phase",
            "assistant_message_start",
            "text_delta",
            "text_delta",
            "assistant_message_end",
            "assistant_phase",
            "done",
        ]
        texts = [e["text"] for e in env.events if e["type"] == "text_delta"]
        assert texts == ["hi ", "there"]


def test_dispatch_user_event_session_records_messages(tmp_path: Path) -> None:
    """dispatch 跑完 user_message + assistant_message 落入 session JSONL。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="hi"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    runtime = AgentRuntime(conversation_factory=lambda sid: conv)
    runtime._dispatch(UserEvent(session_id=conv.session.session_id, user_input="hello"))

    types = [ev.type for ev in conv.session.events]
    assert types == [
        "session_meta",
        "user_message",
        "assistant_phase",
        "assistant_phase",
        "assistant_message",
    ]
    assert conv.session.events[1].payload["content"] == "hello"
    assert conv.session.events[-1].payload["content"] == "hi"


# ===== SystemTriggerEvent user-visibility（AC-5 runtime 层） =====


def test_dispatch_system_trigger_user_visibility_falls_to_listener(tmp_path: Path) -> None:
    """SystemTriggerEvent (output_visibility=user) → conv.dispatch_system_turn 被调 →
    envelope kind=agent_turn + source_kind 透传。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="该睡了"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    runtime = AgentRuntime(conversation_factory=lambda sid: conv)

    with _side_event_loop() as loop:
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"agent_turn"}))
        runtime.listeners.register(sub)

        runtime._dispatch(
            SystemTriggerEvent(
                session_id=conv.session.session_id,
                source_kind="cron:bedtime",
                system_prompt_addendum="现在是约定的休息时间",
                output_visibility="user",
            )
        )

        env = _await_envelope(sub)
        assert env.kind == "agent_turn"
        assert env.source_kind == "cron:bedtime"
        types = [e["type"] for e in env.events]
        assert types == [
            "assistant_phase",
            "assistant_message_start",
            "text_delta",
            "assistant_message_end",
            "assistant_phase",
            "done",
        ]


def test_dispatch_system_trigger_writes_system_trigger_marker(tmp_path: Path) -> None:
    """SystemTriggerEvent dispatch 后 session 里第一条新事件是 system_trigger marker。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="x"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    runtime = AgentRuntime(conversation_factory=lambda sid: conv)
    runtime._dispatch(
        SystemTriggerEvent(
            session_id=conv.session.session_id,
            source_kind="cron:bedtime",
            system_prompt_addendum="hint",
            output_visibility="user",
        )
    )

    types = [ev.type for ev in conv.session.events]
    assert types == [
        "session_meta",
        "system_trigger",
        "assistant_phase",
        "assistant_phase",
        "assistant_message",
    ]


# ===== Subscriber 过滤 =====


def test_subscriber_only_receives_matching_kinds(tmp_path: Path) -> None:
    """订阅者 accept_kinds 只含 agent_turn 时，user_turn envelope 不入队。"""
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="u"), LLMTurnDone(stop_reason="end_turn")],
            [LLMTextDelta(text="a"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)
    runtime = AgentRuntime(conversation_factory=lambda sid: conv)

    with _side_event_loop() as loop:
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"agent_turn"}))
        runtime.listeners.register(sub)

        # user 触发轮——subscriber 不应收到（被 kind 过滤）
        runtime._dispatch(UserEvent(session_id=conv.session.session_id, user_input="hi"))
        # 立即跟一个 agent 触发轮——subscriber 收到（kind=agent_turn）
        runtime._dispatch(
            SystemTriggerEvent(
                session_id=conv.session.session_id,
                source_kind="cron:bedtime",
                system_prompt_addendum="hint",
            )
        )

        env = _await_envelope(sub)
        # 第一个到达的 envelope 应是 agent_turn，不是 user_turn
        assert env.kind == "agent_turn"
        # seq=1（这是该 subscriber 收到的第 1 条）
        assert env.seq == 1


# ===== PreTurn SKIP 真的让 dispatch 跳过 =====


def test_pre_turn_skip_aborts_dispatch(tmp_path: Path) -> None:
    """PreTurn hook 返回 SKIP → conv.stream 完全没被调，session 无新事件。"""
    from agent.runtime import PRE_TURN_SKIP, HookKind

    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="should-not-reach"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    runtime = AgentRuntime(conversation_factory=lambda sid: conv)
    runtime.register_hook(HookKind.PRE_TURN, lambda ev: PRE_TURN_SKIP)

    before_count = len(conv.session.events)
    runtime._dispatch(UserEvent(session_id=conv.session.session_id, user_input="hi"))
    # session 无任何新事件——dispatch 被跳过
    assert len(conv.session.events) == before_count
    # LLM 也没被调用
    assert fake.received == []


# ===== AgentRuntime.tool_hook_invoker 桥接 PreToolUse / PostToolUse =====


def test_tool_hook_invoker_bridges_pre_tool_use_block() -> None:
    """AgentRuntime.tool_hook_invoker 接受 (name, args, default_invoke) → 跑 PreToolUse →
    BLOCK 时不调 default_invoke 直接返回业务级失败 ToolResult；PostToolUse 旁路仍跑。"""
    from agent.runtime import HookKind, pre_tool_block
    from agent.tools import ToolResult

    runtime = AgentRuntime(conversation_factory=lambda sid: cast(Conversation, None))
    runtime.register_hook(HookKind.PRE_TOOL_USE, lambda n, a: pre_tool_block("denied"))

    post_seen: list[tuple[str, dict[str, Any], ToolResult]] = []
    runtime.register_hook(
        HookKind.POST_TOOL_USE,
        lambda n, a, r: post_seen.append((n, a, r)),
    )

    default_called = False

    def default_invoke() -> ToolResult:
        nonlocal default_called
        default_called = True
        return ToolResult(text="should-not-reach")

    result = runtime.tool_hook_invoker("echo", {"message": "hi"}, default_invoke)
    assert default_called is False
    assert result.is_error is True
    assert result.text == "denied"
    # PostToolUse 旁路仍能看到 BLOCK 结果
    assert len(post_seen) == 1
    assert post_seen[0][2].is_error is True


def test_tool_hook_invoker_proceed_calls_default_and_post_tool_use() -> None:
    from agent.runtime import HookKind
    from agent.tools import ToolResult

    runtime = AgentRuntime(conversation_factory=lambda sid: cast(Conversation, None))
    post_seen: list[ToolResult] = []
    runtime.register_hook(
        HookKind.POST_TOOL_USE,
        lambda n, a, r: post_seen.append(r),
    )

    def default_invoke() -> ToolResult:
        return ToolResult(text="real result")

    result = runtime.tool_hook_invoker("echo", {}, default_invoke)
    assert result.text == "real result"
    assert post_seen == [result]


def test_listener_overflow_unregisters_slow_subscriber() -> None:
    """旁路 subscriber 满时断开，不积累等待 queue.put 的 Future。"""
    from agent import TurnDone

    runtime = AgentRuntime(conversation_factory=lambda sid: cast(Conversation, None))
    event = UserEvent(session_id="session-a", user_input="hello")
    with _side_event_loop() as loop:
        sub = Subscriber(
            loop=loop,
            accept_kinds=frozenset({"user_turn"}),
            queue_maxsize=1,
        )
        runtime.listeners.register(sub)
        done = TurnDone(stop_reason="end_turn", total_tool_calls=0)
        runtime.listeners.fan_out_event(event, done)
        runtime.listeners.fan_out_event(event, done)

        async def barrier() -> None:
            return None

        asyncio.run_coroutine_threadsafe(barrier(), loop).result(timeout=2)
        assert runtime.listeners.subscriber_count() == 0


def test_submit_runs_conversation_and_correlates_session_events(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="runtime"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)
    runtime = AgentRuntime(conversation_factory=lambda sid: conv)
    caplog.set_level(logging.INFO)
    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="hello"),
            run_id="run-045",
            channel="text",
        )
        events = cast(list[ConversationEvent], list(handle.events()))
        assert [event.type for event in events] == [
            "model_response_status",
            "assistant_phase",
            "assistant_message_start",
            "text_delta",
            "assistant_message_end",
            "assistant_phase",
            "done",
        ]
        assert handle.terminal is not None
        assert handle.terminal.outcome is RunOutcome.COMPLETED
        user = next(event for event in conv.session.events if event.type == "user_message")
        assistant = next(
            event for event in conv.session.events if event.type == "assistant_message"
        )
        assert user.meta["run_id"] == "run-045"
        assert assistant.meta["run_id"] == "run-045"
        started = next(event for event in conv.session.events if event.type == "run_started")
        finished = next(event for event in conv.session.events if event.type == "run_terminal")
        snapshot = handle.snapshot()
        assert snapshot.started_at is not None and snapshot.finished_at is not None
        assert started.payload["started_at"] == snapshot.started_at.isoformat()
        assert finished.payload["started_at"] == snapshot.started_at.isoformat()
        assert finished.payload["finished_at"] == snapshot.finished_at.isoformat()
        assert all("started_at" not in str(msg) for msg in fake.received[0])
        latency_text = "\n".join(
            record.getMessage()
            for record in caplog.records
            if "run_id=run-045" in record.getMessage()
        )
        assert "event=scheduler_start" in latency_text
        assert "pre_submit_ms=" in latency_text
        assert "queue_ms=" in latency_text
        assert "event=conversation_bound" in latency_text
        assert "event=conversation_done" in latency_text
        assert "event=run_terminal" in latency_text
        assert "llm_calls=1" in latency_text
        assert "first_text_ms=" in latency_text
    finally:
        runtime.stop()


def test_repeated_malformed_tool_call_recovers_in_same_runtime_run(tmp_path: Path) -> None:
    class RequiredTool:
        name: ClassVar[str] = "required_tool"
        description: ClassVar[str] = "需要完整参数。"
        parameters_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            assert args == {"value": "ok"}
            return ToolResult("ok")

    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "broken-1", "required_tool", "{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(0, "broken-2", "required_tool", "{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(0, "fixed", "required_tool", '{"value":"ok"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_registry=ToolRegistry([RequiredTool()]),
    )
    runtime = AgentRuntime(conversation_factory=lambda _sid: conv)
    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="run"),
            run_id="run-malformed",
        )
        events = cast(list[ConversationEvent], list(handle.events()))

        assert handle.terminal is not None
        assert handle.terminal.outcome is RunOutcome.COMPLETED
        assert handle.terminal.reason_code == "end_turn"
        assert handle.terminal.total_tool_calls == 1
        assert handle.terminal.tool_call_items == 3
        assert handle.terminal.model_calls == 4
        assert handle.terminal.model_continuations == 3
        results = [event for event in events if isinstance(event, ToolCallResult)]
        assert [event.executed for event in results] == [False, False, True]
        done = next(event for event in events if isinstance(event, TurnDone))
        assert done.stop_reason == "end_turn"
        assert done.total_tool_calls == 1
        assert done.tool_call_items == 3
        assert done.model_calls == 4
        assert len(fake.received) == 4
    finally:
        runtime.stop()


def test_submit_forwards_surface_to_default_user_runner(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="runtime"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)
    runtime = AgentRuntime(conversation_factory=lambda sid: conv)
    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="hello"),
            surface=IM_QQ_SURFACE,
        )
        list(handle.events())
        assert any(
            '<surface type="im" provider="qq" />' in message.get("content", "")
            for message in fake.received[-1]
        )
        assert handle.request.surface == IM_QQ_SURFACE
    finally:
        runtime.stop()


def test_submit_forwards_normal_system_turn_tool_context(tmp_path: Path) -> None:
    class EchoTool:
        name: ClassVar[str] = "echo"
        description: ClassVar[str] = "echo"
        parameters_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            self.calls.append(args)
            return ToolResult(text="echoed")

    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "echo-1", "echo", '{"message":"reminder"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_registry=ToolRegistry([echo]),
    )
    runtime = AgentRuntime(conversation_factory=lambda _sid: conv)
    runtime.start()
    try:
        handle = runtime.submit(
            SystemTriggerEvent(
                session_id=conv.session.session_id,
                source_kind="todo_reminder",
                system_prompt_addendum='<system_events>\n[{"type":"todo_reminder"}]\n</system_events>',
                presentation="none",
                tool_mode="normal",
                event_metadata={"batch_id": "batch-1", "occurrence_ids": ["occ-1"]},
            ),
            channel="text",
            surface=IM_QQ_SURFACE,
            metadata={"interaction_destination_id": "qq|bind|c2c|chat|session"},
        )
        events = list(handle.events())

        assert echo.calls == [{"message": "reminder"}]
        assert any(isinstance(event, ToolCallRequest) for event in events)
        assert any(isinstance(event, ToolCallResult) for event in events)
        marker = next(event for event in conv.session.events if event.type == "system_trigger")
        assert marker.meta["batch_id"] == "batch-1"
        assert marker.meta["occurrence_ids"] == ["occ-1"]
        assert any(
            '<surface type="im" provider="qq" />' in message.get("content", "")
            for message in fake.received[0]
        )
    finally:
        runtime.stop()


def test_session_changed_is_published_without_turn_buffer() -> None:
    runtime = AgentRuntime(conversation_factory=lambda _sid: cast(Conversation, None))

    async def scenario() -> None:
        sub = Subscriber(
            loop=asyncio.get_running_loop(), accept_kinds=frozenset({"session_changed"})
        )
        runtime.listeners.register(sub)
        runtime.listeners.publish_session_change(
            SessionChange(
                session_id="s1",
                revision=7,
                change_kind="content",
                origin="openai",
                channel="voice",
            )
        )
        env = await asyncio.wait_for(sub.queue.get(), timeout=1)
        assert env.kind == "session_changed"
        assert env.revision == 7
        assert env.events == []
        assert env.origin == "openai"
        assert env.channel == "voice"
        runtime.listeners.unregister(sub.id)

    asyncio.run(scenario())


def test_conversation_system_run_streams_events_before_terminal_agent_turn() -> None:
    runtime = AgentRuntime(conversation_factory=lambda _sid: cast(Conversation, None))

    async def scenario() -> None:
        sub = Subscriber(
            loop=asyncio.get_running_loop(),
            accept_kinds=frozenset({"run_event", "agent_turn"}),
        )
        runtime.listeners.register(sub)
        trigger = SystemTriggerEvent(
            session_id="s1",
            source_kind="social_auth_completed",
            system_prompt_addendum="continue",
            presentation="conversation",
        )

        runtime.listeners.fan_out_event(trigger, TextDelta(text="正在"), run_id="r1")
        first = await asyncio.wait_for(sub.queue.get(), timeout=1)
        assert first.kind == "run_event"
        assert first.run_id == "r1"
        assert first.events == [{"text": "正在", "type": "text_delta"}]

        runtime.listeners.fan_out_event(
            trigger,
            TurnDone(stop_reason="end_turn", total_tool_calls=0),
            run_id="r1",
        )
        second = await asyncio.wait_for(sub.queue.get(), timeout=1)
        assert second.kind == "run_event"
        assert second.events[0]["type"] == "done"

        runtime.listeners.finish_run(trigger, run_id="r1", outcome="completed")
        terminal = await asyncio.wait_for(sub.queue.get(), timeout=1)
        assert terminal.kind == "agent_turn"
        assert [event["type"] for event in terminal.events] == ["text_delta", "done"]
        runtime.listeners.unregister(sub.id)

    asyncio.run(scenario())


@pytest.mark.parametrize("presentation", ["ambient", "none"])
def test_non_conversation_system_run_does_not_publish_run_event(presentation: str) -> None:
    runtime = AgentRuntime(conversation_factory=lambda _sid: cast(Conversation, None))

    async def scenario() -> None:
        sub = Subscriber(loop=asyncio.get_running_loop(), accept_kinds=frozenset({"run_event"}))
        runtime.listeners.register(sub)
        trigger = SystemTriggerEvent(
            session_id="s1",
            source_kind="social_auth_completed",
            system_prompt_addendum="continue",
            presentation=cast(Any, presentation),
        )
        runtime.listeners.fan_out_event(trigger, TextDelta(text="hidden"), run_id="r1")
        await asyncio.sleep(0)
        assert sub.queue.empty()
        runtime.listeners.unregister(sub.id)

    asyncio.run(scenario())


def test_conversation_cooperative_cancel_persists_seen_partial(tmp_path: Path) -> None:
    from agent.runtime import RunRequest
    from agent.runtime.cancellation import CancelReason, RunCancelled
    from agent.runtime.models import RunHandle

    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="partial"),
                LLMTextDelta(text="unseen"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)
    request = RunRequest(event=UserEvent(session_id=conv.session.session_id, user_input="hello"))
    handle = RunHandle(request)
    stream = conv.stream(
        "hello",
        run_id=request.run_id,
        cancel_check=handle.token.raise_if_cancelled,
    )
    waiting = next(stream)
    assert waiting.type == "model_response_status"
    first = next(event for event in stream if event.type == "text_delta")
    assert first.type == "text_delta"
    assert handle.cancel(CancelReason.CLIENT_DISCONNECTED)
    with pytest.raises(RunCancelled):
        next(stream)
    assistant = next(event for event in conv.session.events if event.type == "assistant_message")
    assert assistant.payload == {
        "content": "partial",
        "partial": True,
        "interruption_reason": "client_disconnected",
        "phase": "unknown",
    }
    assert assistant.meta["run_id"] == request.run_id


def test_conversation_forwards_cancel_check_while_waiting_for_model_stream(
    tmp_path: Path,
) -> None:
    from agent.runtime import RunRequest
    from agent.runtime.cancellation import CancelReason, RunCancelled
    from agent.runtime.models import RunHandle

    fake = _CancelAwareBlockingLanguageModel()
    conv = _make_conversation(tmp_path, fake_llm=fake)
    request = RunRequest(event=UserEvent(session_id=conv.session.session_id, user_input="hello"))
    handle = RunHandle(request)
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            list(
                conv.stream(
                    "hello",
                    run_id=request.run_id,
                    cancel_check=handle.token.raise_if_cancelled,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    assert fake.request_started.wait(1)
    assert handle.cancel(CancelReason.EXPLICIT_REQUEST)
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RunCancelled)
    assistant = next(event for event in conv.session.events if event.type == "assistant_message")
    assert assistant.payload == {
        "content": "",
        "partial": True,
        "interruption_reason": "explicit_request",
        "phase": "unknown",
    }
    assert assistant.meta["run_id"] == request.run_id


def test_cancelled_tool_is_closed_before_same_process_followup(tmp_path: Path) -> None:
    from agent.runtime import RunRequest
    from agent.runtime.cancellation import CancelReason, RunCancelled
    from agent.runtime.models import RunHandle

    tool = _BlockingTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "blocking-1", tool.name, "{}"),
                LLMToolCallDelta(1, "blocking-2", tool.name, "{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="continued"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_registry=ToolRegistry([tool]),
    )
    request = RunRequest(event=UserEvent(session_id=conv.session.session_id, user_input="start"))
    handle = RunHandle(request)
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            list(
                conv.stream(
                    "start",
                    run_id=request.run_id,
                    cancel_check=handle.token.raise_if_cancelled,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    assert tool.started.wait(1)
    assert handle.cancel(CancelReason.EXPLICIT_REQUEST)
    worker.join(timeout=1)
    tool.release.set()
    assert tool.completed.wait(1)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RunCancelled)
    results = [event for event in conv.session.events if event.type == "tool_call_result"]
    assert len(results) == 2
    assert all(event.payload["transaction_status"] == "interrupted" for event in results)
    assert all(event.payload["semantic_status"] == "unknown" for event in results)
    assert all(event.payload["executed"] == "unknown" for event in results)
    assert tool.invocations == 1

    list(conv.stream("follow up", run_id="follow-up"))

    assert tool.invocations == 1
    followup = fake.received[1]
    assistant_index = next(index for index, item in enumerate(followup) if item.get("tool_calls"))
    tool_results = followup[assistant_index + 1 : assistant_index + 3]
    assert [item["role"] for item in tool_results] == ["tool", "tool"]
    assert [item["tool_call_id"] for item in tool_results] == ["blocking-1", "blocking-2"]
    assert any("<turn_aborted>" in str(item.get("content")) for item in followup)


def test_cancelled_command_restores_persisted_partial_output_before_followup(
    tmp_path: Path,
) -> None:
    from agent.runtime import RunRequest
    from agent.runtime.cancellation import CancelReason, RunCancelled
    from agent.runtime.models import RunHandle

    evidence_store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    tool = _BlockingCommandEvidenceTool(evidence_store)
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "command-1", tool.name, "{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="continued"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_registry=ToolRegistry([tool]),
        tool_execution_evidence_store=evidence_store,
    )
    request = RunRequest(event=UserEvent(session_id=conv.session.session_id, user_input="start"))
    handle = RunHandle(request)
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            list(
                conv.stream(
                    "start",
                    run_id=request.run_id,
                    cancel_check=handle.token.raise_if_cancelled,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    try:
        # Wait for the durable evidence checkpoint, not a one-second machine
        # performance budget. The full suite may be doing concurrent disk work.
        assert tool.started.wait(5), errors
        assert handle.cancel(CancelReason.EXPLICIT_REQUEST)
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        # A failed handshake must not leave a tool/consumer running into the
        # next test and turn the original failure into unrelated follow-ups.
        handle.cancel(CancelReason.EXPLICIT_REQUEST)
        tool.release.set()
        worker.join(timeout=5)
    assert len(errors) == 1
    assert isinstance(errors[0], RunCancelled)
    result = next(event for event in conv.session.events if event.type == "tool_call_result")
    content = json.loads(str(result.payload["content"]))
    assert result.payload["transaction_status"] == "interrupted"
    assert result.payload["semantic_status"] == "unknown"
    assert result.payload["evidence_ref"] == content["evidence_ref"]
    assert content["partial"] is True
    assert content["complete"] is False
    assert content["stdout"] == "persisted partial stdout"
    assert content["stderr"] == "persisted partial stderr"
    assert content["dropped_bytes"] == 7

    list(conv.stream("follow up", run_id="follow-up"))

    assert tool.invocations == 1
    followup = fake.received[1]
    restored = next(item for item in followup if item.get("role") == "tool")
    assert restored["content"] == result.payload["content"]


def test_formal_command_result_cleans_transient_evidence_after_commit(tmp_path: Path) -> None:
    evidence_store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    evidence_ref = evidence_store.new_reference()
    conv = _make_conversation(
        tmp_path,
        fake_llm=_ScriptedLLMClient(),
        tool_execution_evidence_store=evidence_store,
    )
    evidence_store.write(
        ToolExecutionEvidence(
            evidence_ref=evidence_ref,
            session_id=conv.session.session_id,
            run_id="run-1",
            tool_call_id="event-call-1",
            operation_id="proc-1",
            tool_name="exec_command",
            process_status="exited",
            output_seq=1,
            dropped_bytes=0,
            stdout="completed output",
            captured_at=datetime.now(UTC).isoformat(),
        )
    )
    conv._session_model_history.append(
        [
            Message(
                role="assistant",
                content="",
                meta={
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "exec_command",
                            "args": {},
                            "arguments_json": "{}",
                        }
                    ]
                },
            )
        ]
    )

    conv._append_tool_call_result_event(
        {
            "id": "call-1",
            "event_id": "event-call-1",
            "name": "exec_command",
            "origin": "model",
        },
        ToolResult(
            text="completed",
            meta={"_execution_evidence_ref": evidence_ref},
        ),
        0.1,
        run_id="run-1",
    )

    assert evidence_store.load(conv.session.session_id, evidence_ref) is None


def test_postprocess_interruption_closes_remaining_tool_results(tmp_path: Path) -> None:
    tool = _BlockingTool()
    tool.release.set()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "fast-1", tool.name, "{}"),
                LLMToolCallDelta(1, "fast-2", tool.name, "{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ]
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_registry=ToolRegistry([tool]),
    )

    def cancel_after_first_result() -> None:
        if any(event.type == "tool_call_result" for event in conv.session.events):
            raise RuntimeError("interrupt-after-first-result")

    with pytest.raises(RuntimeError, match="interrupt-after-first-result"):
        list(
            conv.stream(
                "start",
                run_id="postprocess-interruption",
                cancel_check=cancel_after_first_result,
            )
        )

    results = [event for event in conv.session.events if event.type == "tool_call_result"]
    assert len(results) == 2
    assert results[0].payload.get("transaction_status") is None
    assert results[1].payload["transaction_status"] == "interrupted"
    assert results[1].payload["executed"] == "unknown"
    assert tool.invocations == 2


@pytest.mark.parametrize("preexisting_result", [False, True])
def test_provider_preflight_repairs_terminal_orphan_before_request(
    tmp_path: Path, *, preexisting_result: bool
) -> None:
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="continued"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)
    run_id = "orphaned-run"
    call_id = "orphaned-call"
    event_call_id = "orphaned-event-call"
    assistant = Message(
        role="assistant",
        content="",
        meta={
            "tool_calls": [
                {
                    "id": call_id,
                    "name": "missing_tool",
                    "args": {},
                    "arguments_json": "{}",
                }
            ]
        },
    )
    request = Event(
        type="tool_call_request",
        uuid="orphaned-request",
        ts=datetime.now(UTC),
        payload={
            "tool_call_id": call_id,
            "event_tool_call_id": event_call_id,
            "tool_name": "missing_tool",
            "args": {},
            "execution_disposition": "execute",
        },
        meta={"run_id": run_id, "owner_runtime_instance_id": "old-runtime"},
    )
    terminal = Event(
        type="run_terminal",
        uuid="orphaned-terminal",
        ts=datetime.now(UTC),
        payload={
            "run_id": run_id,
            "status": "interrupted",
            "reason_code": "explicit_request",
            "source": "runtime",
            "recovered": False,
        },
        meta={"run_id": run_id, "owner_runtime_instance_id": "old-runtime"},
    )
    conv._store.append_event(conv.session.session_id, request)
    conv.session.append(request)
    if preexisting_result:
        recovered_result = Event(
            type="tool_call_result",
            uuid="orphaned-recovered-result",
            ts=datetime.now(UTC),
            payload={
                "tool_call_id": call_id,
                "event_tool_call_id": event_call_id,
                "tool_name": "missing_tool",
                "content": "宿主恢复了中断结果。",
                "is_error": True,
                "executed": "unknown",
                "result_origin": "host_reconciliation",
            },
            meta={"run_id": run_id, "owner_runtime_instance_id": "new-runtime"},
        )
        conv._store.append_event(conv.session.session_id, recovered_result)
        conv.session.append(recovered_result)
    conv._store.append_event(conv.session.session_id, terminal)
    conv.session.append(terminal)
    conv._session_model_history.append([assistant])
    conv._session_model_history.note_active_events(conv.session)

    list(conv.stream("follow up", run_id="follow-up"))

    results = [event for event in conv.session.events if event.type == "tool_call_result"]
    assert len(results) == 1
    assert results[0].payload["result_origin"] == (
        "host_reconciliation" if preexisting_result else "history_preflight_repair"
    )
    assert results[0].payload["executed"] == "unknown"
    provider_messages = fake.received[0]
    assistant_index = next(
        index for index, item in enumerate(provider_messages) if item.get("tool_calls")
    )
    assert provider_messages[assistant_index + 1]["role"] == "tool"
    assert provider_messages[assistant_index + 1]["tool_call_id"] == call_id


def test_provider_preflight_blocks_unrepairable_tool_history_locally(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient()
    conv = _make_conversation(tmp_path, fake_llm=fake)
    conv._session_model_history.append(
        [
            Message(
                role="assistant",
                content="",
                meta={
                    "tool_calls": [
                        {
                            "id": "unrepairable-call",
                            "name": "missing_tool",
                            "args": {},
                            "arguments_json": "{}",
                        }
                    ]
                },
            )
        ]
    )

    with pytest.raises(ValueError, match="model_history_tool_transaction_invalid"):
        list(conv.stream("follow up", run_id="follow-up"))

    assert fake.received == []


def test_submit_failure_runs_post_turn_once_with_failed_outcome(tmp_path: Path) -> None:
    from agent.runtime import HookKind, RunOutcome
    from agent.runtime.cancellation import CancellationToken
    from agent.runtime.models import RunRequest

    fake = _ScriptedLLMClient(script=[])
    conv = _make_conversation(tmp_path, fake_llm=fake)
    runtime = AgentRuntime(conversation_factory=lambda sid: conv)
    seen: list[PostTurnContext] = []
    runtime.register_hook(HookKind.POST_TURN, seen.append)

    def fail_runner(
        _conv: Conversation,
        _request: RunRequest,
        _token: CancellationToken,
    ) -> Iterator[ConversationEvent]:
        raise RuntimeError("boom")
        yield  # pragma: no cover

    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="hello"),
            conversation_factory=lambda: conv,
            conversation_runner=fail_runner,
        )
        list(handle.events())
        terminal = handle.terminal
        assert terminal is not None
        assert terminal.outcome is RunOutcome.FAILED
        assert len(seen) == 1
        assert seen[0].outcome is RunOutcome.FAILED
        assert isinstance(seen[0].error, RuntimeError)
    finally:
        runtime.stop()


def test_user_visible_runner_without_turn_done_fails_with_stable_reason(tmp_path: Path) -> None:
    from agent.runtime.cancellation import CancellationToken
    from agent.runtime.models import RunRequest

    conv = _make_conversation(tmp_path, fake_llm=_ScriptedLLMClient(script=[]))
    runtime = AgentRuntime(conversation_factory=lambda _sid: conv)

    def missing_terminal_runner(
        _conv: Conversation,
        _request: RunRequest,
        _token: CancellationToken,
    ) -> Iterator[ConversationEvent]:
        yield TextDelta(text="partial")

    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="hello"),
            conversation_factory=lambda: conv,
            conversation_runner=missing_terminal_runner,
        )
        assert [event.text for event in handle.events() if isinstance(event, TextDelta)] == [
            "partial"
        ]
        terminal = handle.terminal
        assert terminal is not None
        assert terminal.outcome is RunOutcome.FAILED
        assert terminal.reason_code == "missing_turn_terminal"
        assert terminal.turn_status == "failed"
    finally:
        runtime.stop()


def test_cancel_during_registered_preparation_does_not_accept_input_or_run_hooks(
    tmp_path: Path,
) -> None:
    from agent.runtime import RunCancelResult
    from agent.runtime.hooks import PRE_TURN_PROCEED, HookKind, PreTurnDecision

    llm = _ScriptedLLMClient(script=[])
    conv = _make_conversation(tmp_path, fake_llm=llm)
    runtime = AgentRuntime(conversation_factory=lambda _sid: conv)
    entered, release = threading.Event(), threading.Event()
    hooks: list[str] = []

    def prepare(check: Callable[[], None]) -> Conversation:
        entered.set()
        assert release.wait(5)
        check()
        return conv

    def pre_turn(_event: object) -> PreTurnDecision:
        hooks.append("called")
        return PRE_TURN_PROCEED

    runtime.register_hook(HookKind.PRE_TURN, pre_turn)
    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="cancelled preparation"),
            conversation_preparer=prepare,
            input_accepted=False,
            control_scope="desktop_text",
        )
        assert entered.wait(5)
        result, snapshot = runtime.cancel_run(
            handle.run_id,
            expected_session_id=conv.session.session_id,
            required_control_scope="desktop_text",
        )
        assert result is RunCancelResult.ACCEPTED
        assert snapshot is not None and snapshot.input_accepted is False
        assert snapshot.status == "running"
        release.set()
        list(handle.events())
        assert handle.snapshot().outcome is RunOutcome.CANCELLED
        assert handle.snapshot().input_accepted is False
        assert hooks == [] and llm.turn_idx == 0
        assert not any(
            event.type in {"user_message", "assistant_message"} for event in conv.session.events
        )
    finally:
        release.set()
        runtime.stop()


def test_registered_run_accepts_input_only_after_durable_append(tmp_path: Path) -> None:
    llm = _ScriptedLLMClient(script=[[LLMTextDelta(text="ok"), LLMTurnDone()]])
    conv = _make_conversation(tmp_path, fake_llm=llm)
    runtime = AgentRuntime(conversation_factory=lambda _sid: conv)

    def prepare(check: Callable[[], None]) -> Conversation:
        check()
        return conv

    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="durable input"),
            conversation_preparer=prepare,
            input_accepted=False,
        )
        list(handle.events())
        assert handle.snapshot().input_accepted is True
        assert handle.snapshot().outcome is RunOutcome.COMPLETED
        assert any(event.type == "user_message" for event in conv.session.events)
    finally:
        runtime.stop()


def test_memory_preparation_cancel_keeps_accepted_input_without_starting_model(
    tmp_path: Path,
) -> None:
    from agent.runtime import RunCancelResult

    llm = _ScriptedLLMClient(script=[])
    conv = _make_conversation(tmp_path, fake_llm=llm)
    runtime = AgentRuntime(conversation_factory=lambda _sid: conv)

    class CancellingMemory:
        def retrieve(self, _query: str, *, cancel_check: Callable[[], None], **_kwargs: Any) -> Any:
            snapshot = runtime.get_run_snapshot("memory-cancel")
            assert snapshot is not None and snapshot.input_accepted is True
            result, _ = runtime.cancel_run(
                "memory-cancel",
                expected_session_id=conv.session.session_id,
                required_control_scope="desktop_text",
            )
            assert result is RunCancelResult.ACCEPTED
            cancel_check()
            raise AssertionError("memory cancellation must escape")

    conv._memory = cast(Any, CancellingMemory())
    runtime.start()
    try:
        handle = runtime.submit(
            UserEvent(session_id=conv.session.session_id, user_input="keep accepted input"),
            run_id="memory-cancel",
            input_accepted=False,
            control_scope="desktop_text",
        )
        list(handle.events())
        assert handle.snapshot().outcome is RunOutcome.CANCELLED
        assert handle.snapshot().input_accepted is True
        assert llm.turn_idx == 0
        assert sum(event.type == "user_message" for event in conv.session.events) == 1
        assert sum(event.type == "run_terminal" for event in conv.session.events) == 1
    finally:
        runtime.stop()
