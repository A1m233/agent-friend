"""014 单测：``Conversation.dispatch_system_turn`` 的两条 visibility 路径。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §6
（dispatch_system_turn 入口）的核心行为：

- ``output_visibility="user"``：与 :meth:`Conversation.stream` 同形
  yield TextDelta + TurnDone，并落 ``assistant_message`` 事件
- ``output_visibility="memory_only"``（silent turn）：**不 yield 任何事件**，
  不写 ``assistant_message``、写 ``memory_observation``、自构 fragment 喂 memory；
  ``session.messages`` 派生不包含 silent text（历史天然干净，避免污染 LLM 上下文）
- 两条路径都先落一条 ``system_trigger`` marker

对应 requirement.md AC-5 / AC-6 的最小验证；端到端验证由 M14.3 / M14.8 补。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

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
    TurnDone,
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
    """按脚本依次回放 stream 输出。"""

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
    """记录 observe 调用 + retrieve 返回空。仅满足 Conversation 用到的两个方法。"""

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

    from memory import Memory  # 仅 cast 用

    return Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, fake_llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        memory=cast(Memory, memory) if memory is not None else None,
    )


# ===== output_visibility="user" =====


def test_dispatch_system_turn_user_visibility_yields_text_and_turndone(tmp_path: Path) -> None:
    """user visibility 路径与 stream 同形：yield TextDelta(s) + TurnDone。"""
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="很晚"),
                LLMTextDelta(text="了"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    events = list(
        conv.dispatch_system_turn(
            source_kind="cron:bedtime",
            system_prompt_addendum="现在是约定的休息时间，按 persona 自然说一句。",
            output_visibility="user",
        )
    )

    text_deltas = [ev for ev in events if isinstance(ev, TextDelta)]
    turn_dones = [ev for ev in events if isinstance(ev, TurnDone)]
    assert [d.text for d in text_deltas] == ["很晚", "了"]
    assert len(turn_dones) == 1
    assert turn_dones[0].stop_reason == "end_turn"
    assert turn_dones[0].total_tool_calls == 0


def test_dispatch_system_turn_user_visibility_writes_assistant_message(tmp_path: Path) -> None:
    """user visibility 路径在 session 中落 assistant_message 事件。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="hi"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    list(
        conv.dispatch_system_turn(
            source_kind="cron:bedtime",
            system_prompt_addendum="该睡了",
            output_visibility="user",
        )
    )

    types = [ev.type for ev in conv.session.events]
    # session_meta + system_trigger + assistant_message
    assert types == ["session_meta", "system_trigger", "assistant_message"]
    assert conv.session.events[2].payload["content"] == "hi"


# ===== output_visibility="memory_only"（silent turn） =====


def test_dispatch_system_turn_memory_only_yields_nothing(tmp_path: Path) -> None:
    """silent turn 不向上游 yield 任何 ConversationEvent。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="reflection"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)

    events = list(
        conv.dispatch_system_turn(
            source_kind="idle_reflection",
            system_prompt_addendum="抽取最近事实",
            output_visibility="memory_only",
        )
    )
    assert events == []


def test_dispatch_system_turn_memory_only_writes_observation_not_assistant(
    tmp_path: Path,
) -> None:
    """silent turn 写 memory_observation 事件、**不写** assistant_message。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="agent self-note"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)

    list(
        conv.dispatch_system_turn(
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )

    types = [ev.type for ev in conv.session.events]
    # session_meta + system_trigger + memory_observation （**没有** assistant_message）
    assert types == ["session_meta", "system_trigger", "memory_observation"]
    assert "assistant_message" not in types
    obs = conv.session.events[2]
    assert obs.payload["text"] == "agent self-note"
    assert obs.payload["source_kind"] == "idle_reflection"


def test_dispatch_system_turn_memory_only_feeds_memory_with_agent_utterance(
    tmp_path: Path,
) -> None:
    """silent turn 自构 fragment 喂 memory.observe，speaker=agent、1 条 utterance、
    source_ref 与 memory_observation event 的 uuid 关联。"""
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="user has been working hard"), LLMTurnDone(stop_reason="end_turn")]
        ]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)

    list(
        conv.dispatch_system_turn(
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )

    assert len(fake_mem.observed) == 1
    frag = fake_mem.observed[0]
    assert frag.session_id == conv.session.session_id
    assert len(frag.utterances) == 1
    u = frag.utterances[0]
    assert u.speaker == "agent"
    assert u.text == "user has been working hard"
    # source_ref 形如 "{session_id}#{event_uuid}"，定位回 memory_observation event
    obs_event = next(ev for ev in conv.session.events if ev.type == "memory_observation")
    assert u.source_ref == f"{conv.session.session_id}#{obs_event.uuid}"


def test_dispatch_system_turn_memory_only_does_not_pollute_messages(tmp_path: Path) -> None:
    """silent turn 写的 memory_observation 不参与 ``session.messages`` 派生——
    LLM 上下文构造看不到，避免"用户没听过"的内容污染对话历史。"""
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="agent self-note"), LLMTurnDone(stop_reason="end_turn")]]
    )
    fake_mem = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=fake_mem)

    list(
        conv.dispatch_system_turn(
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )

    # messages 派生只识别 user_message / assistant_message / tool_call_*——
    # system_trigger / memory_observation 跟 compaction 一样不产生 Message
    msgs = conv.session.messages
    contents = [m.content for m in msgs]
    assert "agent self-note" not in contents
    # 此时 session 里完全没有任何对话消息——messages 应为空
    assert msgs == []


# ===== system_trigger marker（两路径共享） =====


def test_dispatch_system_turn_writes_system_trigger_marker(tmp_path: Path) -> None:
    """两条 visibility 路径都先落一条 system_trigger 事件，payload 含 source_kind /
    addendum / visibility 三字段。"""
    fake = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    conv = _make_conversation(tmp_path, fake_llm=fake)

    list(
        conv.dispatch_system_turn(
            source_kind="cron:bedtime",
            system_prompt_addendum="该睡了",
            output_visibility="user",
        )
    )

    triggers = [ev for ev in conv.session.events if ev.type == "system_trigger"]
    assert len(triggers) == 1
    p = triggers[0].payload
    assert p["source_kind"] == "cron:bedtime"
    assert p["system_prompt_addendum"] == "该睡了"
    assert p["output_visibility"] == "user"


# ===== 021：trailing_user 注入路径 =====


def test_dispatch_system_turn_injects_trailing_user(tmp_path: Path) -> None:
    """021：dispatch_system_turn 把 addendum 注入为 role="user" trailing 消息，
    而不是 role="system"——LLM 看到的 messages 最后一条 role=user + content=addendum。
    两条 visibility 路径共用这一行 _assemble 调用，两路径都验证。"""
    addendum = "<system_trigger>该睡了</system_trigger>"

    # 路径 1: user visibility
    fake = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    conv = _make_conversation(tmp_path, fake_llm=fake)
    list(
        conv.dispatch_system_turn(
            source_kind="cron:bedtime",
            system_prompt_addendum=addendum,
            output_visibility="user",
        )
    )
    assert len(fake.received) == 1
    last_msg = fake.received[0][-1]
    assert last_msg["role"] == "user", (
        f"user visibility 路径末尾 role 应是 user，实际 {last_msg['role']}"
    )
    assert last_msg["content"] == addendum

    # 路径 2: silent turn (memory_only)
    fake_silent = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    fake_mem = _FakeMemory()
    conv_silent = _make_conversation(tmp_path / "silent", fake_llm=fake_silent, memory=fake_mem)
    list(
        conv_silent.dispatch_system_turn(
            source_kind="idle_reflection",
            system_prompt_addendum=addendum,
            output_visibility="memory_only",
        )
    )
    assert len(fake_silent.received) == 1
    last_msg = fake_silent.received[0][-1]
    assert last_msg["role"] == "user", f"silent 路径末尾 role 应是 user，实际 {last_msg['role']}"
    assert last_msg["content"] == addendum


def test_dispatch_system_turn_does_not_persist_trailing_user(tmp_path: Path) -> None:
    """021：trailing_user 注入只活在 LLM 视图，session.events 仍只落 system_trigger
    marker（payload 含 addendum 文本），不出现"trailing_user 消息被落盘"为 user_message。"""
    addendum = "<system_trigger>该睡了</system_trigger>"
    fake = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    conv = _make_conversation(tmp_path, fake_llm=fake)
    list(
        conv.dispatch_system_turn(
            source_kind="cron:bedtime",
            system_prompt_addendum=addendum,
            output_visibility="user",
        )
    )
    types = [ev.type for ev in conv.session.events]
    # 没有任何 user_message —— 即使 trailing_user 是 role=user 注入，也不会被持久化
    assert "user_message" not in types
