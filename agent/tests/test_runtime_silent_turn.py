"""014 单测：``AgentRuntime`` 的 silent turn 路径
（``SystemTriggerEvent.output_visibility="memory_only"``）。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §3.3 / §5；
对应 requirement.md AC-6（IdleReflectionSource silent turn 端到端，runtime 这一层）。

关键不变量：
- listener fan-out 收到 **0** 条 envelope（silent turn 不 yield ConversationEvent）
- session 落事件包含 system_trigger + memory_observation，**没有** assistant_message
- session.messages 派生**不包含**反思文本（历史天然干净）
- ``memory.observe`` 总共被调 **1 次**（来自 conversation.dispatch_system_turn 自完成；
  AgentRuntime 默认 PostTurn observe hook **跳过** silent turn 避免重复）
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
    Subscriber,
    SystemTriggerEvent,
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
from memory import ConversationFragment, MemoryContext


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


class _FakeMemory:
    def __init__(self) -> None:
        self.observed: list[ConversationFragment] = []

    def observe(self, fragment: ConversationFragment) -> None:
        self.observed.append(fragment)

    def retrieve(self, query: str, **kwargs: Any) -> MemoryContext:
        return MemoryContext.empty()


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: _ScriptedLLMClient,
    memory: _FakeMemory | None = None,
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

    from memory import Memory

    return Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, fake_llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        memory=cast(Memory, memory) if memory is not None else None,
        post_turn_external=True,
    )


@contextmanager
def _side_event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="SideLoop")
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        loop.close()


# ===== 主路径：silent turn 完整不可见 =====


def test_silent_turn_yields_no_envelope_to_listener(tmp_path: Path) -> None:
    """silent turn (output_visibility=memory_only) → listener 收不到任何 envelope。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="reflection"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)
    from memory import Memory

    runtime = AgentRuntime(
        conversation_factory=lambda sid: conv,
        memory=cast(Memory, fake_mem),
    )

    with _side_event_loop() as loop:
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"agent_turn", "user_turn"}))
        runtime.listeners.register(sub)

        runtime._dispatch(
            SystemTriggerEvent(
                session_id=conv.session.session_id,
                source_kind="idle_reflection",
                system_prompt_addendum="reflect",
                output_visibility="memory_only",
            )
        )

        # listener queue 应该为空——silent turn 不冒泡
        # 用 run_coroutine_threadsafe 跑一次短超时 get，应当超时
        fut = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(sub.queue.get(), timeout=0.2),
            loop,
        )
        try:
            fut.result(timeout=1.0)
        except TimeoutError:
            pass  # ✓ 这是期望路径
        else:
            raise AssertionError("silent turn 不应该向 listener 推 envelope")


def test_silent_turn_writes_marker_and_observation_not_assistant(tmp_path: Path) -> None:
    """silent turn → session 落 system_trigger + memory_observation；**没有** assistant_message。"""
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="user has been working hard"), LLMTurnDone(stop_reason="end_turn")]
        ]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)
    from memory import Memory

    runtime = AgentRuntime(
        conversation_factory=lambda sid: conv,
        memory=cast(Memory, fake_mem),
    )
    runtime._dispatch(
        SystemTriggerEvent(
            session_id=conv.session.session_id,
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )

    types = [ev.type for ev in conv.session.events]
    assert types == ["session_meta", "system_trigger", "memory_observation"]
    assert "assistant_message" not in types
    obs = next(ev for ev in conv.session.events if ev.type == "memory_observation")
    assert obs.payload["text"] == "user has been working hard"
    assert obs.payload["source_kind"] == "idle_reflection"


def test_silent_turn_does_not_pollute_session_messages(tmp_path: Path) -> None:
    """silent turn 的反思文本**不进入** session.messages 派生——下次 LLM 上下文构造看不到。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="self-note"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)
    from memory import Memory

    runtime = AgentRuntime(
        conversation_factory=lambda sid: conv,
        memory=cast(Memory, fake_mem),
    )
    runtime._dispatch(
        SystemTriggerEvent(
            session_id=conv.session.session_id,
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )

    msgs = conv.session.messages
    # silent turn 后 messages 仍为空（没有 user / assistant）——LLM 历史天然干净
    assert msgs == []


# ===== memory.observe 只被调一次（默认 PostTurn hook 跳过 silent turn 避免重复） =====


def test_silent_turn_calls_memory_observe_exactly_once(tmp_path: Path) -> None:
    """silent turn 路径：conversation.dispatch_system_turn 内部自构 fragment 喂 memory；
    AgentRuntime 的默认 PostTurn observe hook **跳过** silent turn——总共 observe 一次。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="reflection text"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)
    from memory import Memory

    runtime = AgentRuntime(
        conversation_factory=lambda sid: conv,
        memory=cast(Memory, fake_mem),
    )
    runtime._dispatch(
        SystemTriggerEvent(
            session_id=conv.session.session_id,
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )

    # 关键不变量：memory.observe 总共调一次（不是 0 不是 2）
    assert len(fake_mem.observed) == 1
    frag = fake_mem.observed[0]
    assert len(frag.utterances) == 1
    u = frag.utterances[0]
    assert u.speaker == "agent"
    assert u.text == "reflection text"
    # source_ref 关联到落入 session 的 memory_observation event
    obs_event = next(ev for ev in conv.session.events if ev.type == "memory_observation")
    assert u.source_ref == f"{conv.session.session_id}#{obs_event.uuid}"
