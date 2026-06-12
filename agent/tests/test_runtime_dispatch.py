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
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from agent.runtime import (
    AgentRuntime,
    PushEnvelope,
    Subscriber,
    SystemTriggerEvent,
    UserEvent,
)

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
)


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
        llm_client=cast(LLMClient, fake_llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
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
        # events 内含 TextDelta * 2 + TurnDone
        types = [e["type"] for e in env.events]
        assert types == ["text_delta", "text_delta", "done"]
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
    assert types == ["session_meta", "user_message", "assistant_message"]
    assert conv.session.events[1].payload["content"] == "hello"
    assert conv.session.events[2].payload["content"] == "hi"


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
        assert types == ["text_delta", "done"]


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
    assert types == ["session_meta", "system_trigger", "assistant_message"]


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
