"""014 单测：``_observe_turn`` 迁移行为零退化（PostTurn 默认 hook == 老 finally 路径）。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §5 末尾
（``_default_post_turn_observe``）+ §7 + AC-4 的字段清单。

迁移核心不变量（来自需求 §5 / R-4.5.2）：

- ``memory.observe`` 收到的 :class:`ConversationFragment` 形状完全不变
- 关键字段（user 原话、role、时序、source_ref）逐项相等
- 整体序列化 hash 一致兜底

测试构造：用同一个 ``Conversation`` + 同一份 ``session.events``，
分别走老路径（直接调 ``project_turn``）与新路径（``AgentRuntime``
``_default_post_turn_observe``），断言产出 fragment 完全相等。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from agent.memory_feed import project_turn
from agent.runtime import (
    AgentRuntime,
    PostTurnContext,
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
    memory: _FakeMemory | None,
    post_turn_external: bool,
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
        post_turn_external=post_turn_external,
    )


# ===== 老 finally 路径 vs 新 PostTurn hook：两条都喂 memory，但只一条该跑 =====


def test_post_turn_external_false_runs_finally_observe(tmp_path: Path) -> None:
    """老路径默认行为：``post_turn_external=False`` → finally 块调老 _observe_turn → memory.observe 被调一次。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="hi"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem, post_turn_external=False)

    list(conv.stream("hello"))

    assert len(fake_mem.observed) == 1


def test_post_turn_external_true_skips_finally_observe(tmp_path: Path) -> None:
    """新路径：``post_turn_external=True`` → finally 不调 _observe_turn → memory.observe 不被调（由外部接管）。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="hi"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem, post_turn_external=True)

    list(conv.stream("hello"))

    assert fake_mem.observed == []


# ===== AC-4 字段级断言（核心）=====


def test_observe_migration_fragment_field_equivalence(tmp_path: Path) -> None:
    """同一 session.events，老 finally 路径与新 PostTurn 默认 hook 都产出
    **完全相等**的 :class:`ConversationFragment`——证明迁移零退化。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="hi there"), LLMTurnDone(stop_reason="end_turn")]]
    )
    # 用同一个 Conversation：post_turn_external=True 让 finally 不 observe，
    # 这样我们能控制 memory.observe 何时被调，便于对照
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem, post_turn_external=True)
    list(conv.stream("hello"))
    # 此时 fake_mem.observed == []（finally 跳过）
    assert fake_mem.observed == []

    # 拿本轮新增事件切片
    turn_start_idx = 1  # session_meta 在 index 0
    new_events = conv.session.events[turn_start_idx:]
    persona_id = conv.session.current_persona_id or ""

    # --- Path A: 模拟老 _observe_turn 行为（直接调 project_turn）---
    fragment_old = project_turn(
        new_events,
        session_id=conv.session.session_id,
        persona_id=persona_id,
    )

    # --- Path B: AgentRuntime 默认 PostTurn hook ---
    from memory import Memory

    runtime = AgentRuntime(
        conversation_factory=lambda sid: conv,
        memory=cast(Memory, fake_mem),
    )
    runtime._default_post_turn_observe(
        PostTurnContext(
            session=conv.session,
            turn_start_idx=turn_start_idx,
            event=UserEvent(session_id=conv.session.session_id, user_input="hello"),
        )
    )
    assert len(fake_mem.observed) == 1
    fragment_new = fake_mem.observed[0]

    # AC-4 (a): 关键字段逐项相等
    assert fragment_old.session_id == fragment_new.session_id
    assert fragment_old.persona_id == fragment_new.persona_id
    assert fragment_old.owner_user_id == fragment_new.owner_user_id
    assert len(fragment_old.utterances) == len(fragment_new.utterances)
    for u_old, u_new in zip(fragment_old.utterances, fragment_new.utterances, strict=True):
        assert u_old.speaker == u_new.speaker  # user / agent
        assert u_old.text == u_new.text  # user 原话 + agent 文本
        assert u_old.ts == u_new.ts  # 时序——同 event ts，必相等
        assert u_old.source_ref == u_new.source_ref  # "{sid}#{event_uuid}"

    # AC-4 (b): 整体序列化 hash 一致兜底——frozen dataclass + list 顺序相等
    # → ==（即基于全字段相等的 hash 一致）
    assert fragment_old == fragment_new


def test_observe_migration_with_assistant_text_round_trip(tmp_path: Path) -> None:
    """带 assistant 多段文本的 turn：utterances 应含 user + assistant 两条，两条路径完全一致。"""
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="第一段"),
                LLMTextDelta(text="第二段"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem, post_turn_external=True)
    list(conv.stream("用户说的话"))

    turn_start_idx = 1
    new_events = conv.session.events[turn_start_idx:]
    persona_id = conv.session.current_persona_id or ""

    fragment_old = project_turn(
        new_events,
        session_id=conv.session.session_id,
        persona_id=persona_id,
    )

    from memory import Memory

    runtime = AgentRuntime(
        conversation_factory=lambda sid: conv,
        memory=cast(Memory, fake_mem),
    )
    runtime._default_post_turn_observe(
        PostTurnContext(
            session=conv.session,
            turn_start_idx=turn_start_idx,
            event=UserEvent(session_id=conv.session.session_id, user_input="用户说的话"),
        )
    )

    fragment_new = fake_mem.observed[0]

    # utterances 必须含 user + agent 两条且内容正确
    assert len(fragment_new.utterances) == 2
    assert fragment_new.utterances[0].speaker == "user"
    assert fragment_new.utterances[0].text == "用户说的话"
    assert fragment_new.utterances[1].speaker == "agent"
    assert fragment_new.utterances[1].text == "第一段第二段"

    # 两条路径完全一致
    assert fragment_old == fragment_new


# ===== Edge case: memory 未注入时默认 hook no-op =====


def test_default_post_turn_observe_skips_when_memory_is_none(tmp_path: Path) -> None:
    """memory=None 时默认 PostTurn hook 直接 return，不抛、不副作用。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="x"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=None, post_turn_external=True)
    list(conv.stream("hi"))

    runtime = AgentRuntime(conversation_factory=lambda sid: conv, memory=None)
    # 不抛——这就是验收
    runtime._default_post_turn_observe(
        PostTurnContext(
            session=conv.session,
            turn_start_idx=1,
            event=UserEvent(session_id=conv.session.session_id, user_input="hi"),
        )
    )
