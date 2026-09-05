from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from agent.assistant_phase import PhaseRun, normalize_phase
from agent.sessions import (
    SessionCacheEpoch,
    SessionModelContext,
    SessionModelHistory,
    SessionRolloutRecorder,
    restore_rollout,
)
from llm_providers.deepseek_vision_compat import chat_replay_messages
from llm_providers.responses.request import prepare_request

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    AssistantMessagePhase,
    AssistantPhaseStatus,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    Session,
    TextDelta,
)
from llm_providers import (
    LanguageModelCapabilities,
    LLMAssistantItemDone,
    LLMAssistantItemStart,
    LLMRetryScheduled,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderAssistantItem,
    ProviderAssistantTurn,
    ProviderToolCall,
    build_provider_spec,
)
from memory import ConversationFragment, Memory, MemoryContext


@dataclass
class _ScriptedLLMClient:
    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    turn_idx: int = 0
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("unexpected generate")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


class _FakeMemory:
    def __init__(self) -> None:
        self.observed: list[ConversationFragment] = []

    def retrieve(self, query: str, **kwargs: Any) -> MemoryContext:
        return MemoryContext.empty()

    def observe(self, fragment: ConversationFragment) -> None:
        self.observed.append(fragment)


def _make_conversation(
    tmp_path: Path, *, fake_llm: _ScriptedLLMClient, memory: _FakeMemory | None = None
) -> Conversation:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="phase fixture",
        persona="default",
        persona_id=persona,
        model="deepseek/deepseek-v4-flash",
    )
    store.create(session)
    return Conversation(
        session=session,
        store=store,
        llm_client=fake_llm,
        context_manager=NaiveContextManager(),
        catalog=catalog,
        prompt_builder=MarkdownPromptBuilder(persona_id=persona, catalog=catalog),
        prompt_builder_factory=lambda pid: MarkdownPromptBuilder(persona_id=pid, catalog=catalog),
        memory=cast(Memory, memory) if memory is not None else None,
    )


class NativeModel(_ScriptedLLMClient):
    capabilities: ClassVar[LanguageModelCapabilities] = LanguageModelCapabilities("native")


def items() -> list[LLMStreamEvent]:
    # First observed delta is not the first source item.
    return [
        LLMAssistantItemStart("a", 0, "commentary"),
        LLMAssistantItemStart("b", 1, "commentary"),
        LLMAssistantItemStart("c", 2, "final_answer"),
        LLMTextDelta("结", item_id="c", output_index=2, phase="final_answer"),
        LLMTextDelta("查", item_id="a", output_index=0, phase="commentary"),
        LLMTextDelta("验证", item_id="b", output_index=1, phase="commentary"),
        LLMTextDelta("询", item_id="a", output_index=0, phase="commentary"),
        LLMTextDelta("果", item_id="c", output_index=2, phase="final_answer"),
        LLMAssistantItemDone("a", 0, "commentary"),
        LLMAssistantItemDone("b", 1, "commentary"),
        LLMAssistantItemDone("c", 2, "final_answer"),
        LLMTurnDone(stop_reason="end_turn"),
    ]


def test_item_identity_persistence_history_and_memory(tmp_path: Path) -> None:
    model = NativeModel(script=[items(), items()])
    memory = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=model, memory=memory)
    streamed = list(conv.stream("检查", run_id="run-one", user_event_id="user-one"))
    messages = [ev for ev in conv.session.active_events if ev.type == "assistant_message"]
    assert [ev.payload["content"] for ev in messages] == ["查询", "验证", "结果"]
    assert [ev.payload["phase"] for ev in messages] == ["commentary", "commentary", "final_answer"]
    assert {ev.message_id for ev in streamed if isinstance(ev, TextDelta)} == {
        ev.uuid for ev in messages
    }
    assert [
        (ev.mode, ev.validation) for ev in streamed if isinstance(ev, AssistantPhaseStatus)
    ] == [
        ("native", "pending"),
        ("native", "valid"),
    ]
    assert len(memory.observed) == 1
    assert "commentary" not in str(memory.observed[0])
    assert "查询" in str(memory.observed[0])
    restored = JsonlSessionStore(base_dir=tmp_path / "sessions").load(conv.session.session_id)
    assert [
        msg.to_openai().get("phase") for msg in restored.messages if msg.role == "assistant"
    ] == [
        "commentary",
        "commentary",
        "final_answer",
    ]
    list(conv.stream("继续", run_id="run-two"))
    assert [msg.get("phase") for msg in model.received[-1] if msg.get("role") == "assistant"] == [
        "commentary",
        "commentary",
        "final_answer",
    ]
    assert "可见消息阶段" in model.received[0][0]["content"]


@pytest.mark.parametrize("final_phase", ["commentary", "final_answer", None])
def test_lifecycle_updates_do_not_repeat_text_or_memory(tmp_path: Path, final_phase: Any) -> None:
    events: list[LLMStreamEvent] = [
        LLMAssistantItemStart("item", 0, "final_answer"),
        LLMTextDelta("hello", item_id="item", output_index=0, phase="final_answer"),
        LLMAssistantItemDone("item", 0, "commentary"),
        LLMTurnDone(
            assistant_turn=ProviderAssistantTurn(
                "hello", text_items=(ProviderAssistantItem("item", 0, "hello", final_phase),)
            )
        ),
    ]
    model = NativeModel(script=[events, items()])
    memory = _FakeMemory()
    conv = _make_conversation(tmp_path, fake_llm=model, memory=memory)
    streamed = list(conv.stream("hi", run_id="phase-update"))
    assert [ev.text for ev in streamed if isinstance(ev, TextDelta)] == ["hello"]
    messages = [ev for ev in conv.session.active_events if ev.type == "assistant_message"]
    assert len(messages) == 1
    assert messages[0].payload["phase"] == (final_phase or "unknown")
    updates = [ev for ev in streamed if isinstance(ev, AssistantMessagePhase)]
    assert all(ev.message_id == messages[0].uuid for ev in updates)
    assert updates[-1].phase == (final_phase or "unknown")
    assert len(memory.observed) == 1
    restored = JsonlSessionStore(base_dir=tmp_path / "sessions").load(conv.session.session_id)
    assistant = [msg for msg in restored.messages if msg.role == "assistant"]
    assert assistant[0].to_openai().get("phase") == final_phase
    list(conv.stream("next", run_id="next"))
    replay = [msg for msg in model.received[-1] if msg.get("role") == "assistant"]
    assert replay[0].get("phase") == final_phase


def test_idempotent_replay_retains_item_ids_and_original_run_mode(tmp_path: Path) -> None:
    model = NativeModel(script=[items()])
    conv = _make_conversation(tmp_path, fake_llm=model)
    first = list(conv.stream("hi", run_id="native-run", user_event_id="u"))
    # Different bound model does not reclassify the persisted native run.
    conv._llm_client = _ScriptedLLMClient()
    replay = list(conv.stream("hi", user_event_id="u", run_id="replay-run"))
    first_ids = {ev.message_id for ev in first if isinstance(ev, TextDelta)}
    assert {ev.message_id for ev in replay if isinstance(ev, TextDelta)} == first_ids
    assert [ev.mode for ev in replay if isinstance(ev, AssistantPhaseStatus)] == ["native"]
    assert [ev.run_id for ev in replay if isinstance(ev, AssistantPhaseStatus)] == ["replay-run"]
    assert model.turn_idx == 1


def test_partial_items_are_visible_and_do_not_become_final(tmp_path: Path) -> None:
    class Broken(NativeModel):
        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            yield LLMAssistantItemStart("a", 0, "commentary")
            yield LLMTextDelta("已开始", item_id="a", phase="commentary", output_index=0)
            yield LLMAssistantItemDone("a", 0, "commentary")
            yield LLMAssistantItemStart("b", 1, "final_answer")
            yield LLMTextDelta("未完成", item_id="b", phase="final_answer", output_index=1)
            raise RuntimeError("fixture interruption")

    conv = _make_conversation(tmp_path, fake_llm=Broken())
    with pytest.raises(RuntimeError, match="fixture interruption"):
        list(conv.stream("hi", run_id="partial"))
    messages = [ev for ev in conv.session.active_events if ev.type == "assistant_message"]
    assert [ev.payload["partial"] for ev in messages] == [True, True]
    assert [ev.payload["content"] for ev in messages] == ["已开始", "未完成"]
    assert [
        ev.payload["validation"]
        for ev in conv.session.active_events
        if ev.type == "assistant_phase"
    ] == ["pending"]


@pytest.mark.parametrize("phase", [None, "future", "final"])
def test_unknown_does_not_infer_from_position_or_tools(phase: object) -> None:
    assert normalize_phase(phase) == "unknown"
    state = PhaseRun("native", "r")
    state.text(LLMTextDelta("last", item_id="x"))
    state.items["x"].done = True
    state.finish(LLMTurnDone())
    assert state.items["x"].phase == "unknown"
    assert state.validation == "missing_final"


def test_native_item_id_cannot_collide_with_legacy_fallback() -> None:
    state = PhaseRun("native", "r")
    state.start(LLMAssistantItemStart("legacy", 0, "final_answer"))
    state.text(LLMTextDelta("answer", item_id="legacy", phase="final_answer"))
    state.items["legacy"].done = True
    state.finish(LLMTurnDone())
    assert state.provider_items()[0].phase == "final_answer"
    assert state.provider_items()[0].item_id == "legacy"


@pytest.mark.parametrize("tool_index", [0, 2])
def test_final_then_tool_disables_whole_run_without_rewriting_phase(tool_index: int) -> None:
    state = PhaseRun("native", "r")
    state.start(LLMAssistantItemStart("x", 1, "final_answer"))
    state.text(LLMTextDelta("finished", item_id="x", phase="final_answer"))
    state.items["x"].done = True
    state.finish(
        LLMTurnDone(
            assistant_turn=ProviderAssistantTurn(
                "finished",
                (ProviderToolCall(0, "c", "lookup", "{}", tool_index),),
            )
        )
    )
    assert state.validation == "conflict"
    assert state.items["x"].phase == "final_answer"
    state.begin_response()
    state.finish(LLMTurnDone())
    assert state.validation == "conflict"


def test_native_silent_reflection_has_no_visible_events(tmp_path: Path) -> None:
    model = NativeModel(script=[items()])
    conv = _make_conversation(tmp_path, fake_llm=model)
    assert (
        list(
            conv.dispatch_system_turn(
                source_kind="idle_reflection",
                system_prompt_addendum="reflect",
                output_visibility="memory_only",
            )
        )
        == []
    )
    assert not any(
        ev.type in {"assistant_phase", "assistant_message"} for ev in conv.session.events
    )
    assert "可见消息阶段" not in model.received[0][0]["content"]


def test_empty_item_retry_does_not_duplicate_text_or_phase(tmp_path: Path) -> None:
    model = NativeModel(
        script=[
            [
                LLMAssistantItemStart("abandoned", 0, "final_answer"),
                LLMRetryScheduled(),
                *items(),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=model)
    output = list(conv.stream("hi", run_id="retry"))
    assert len({ev.message_id for ev in output if isinstance(ev, TextDelta)}) == 3
    assert [ev.validation for ev in output if isinstance(ev, AssistantPhaseStatus)] == [
        "pending",
        "valid",
    ]
    assert len([ev for ev in conv.session.events if ev.type == "assistant_message"]) == 3


def test_phase_rollout_and_cross_api_tool_replay(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="replay", persona="default", model="deepseek/deepseek-v4-flash")
    store.create(session)
    history = SessionModelHistory()
    assistant = ProviderAssistantTurn(
        "beforeafter",
        (
            ProviderToolCall(0, "c1", "lookup", '{ "n": 1 }', 1),
            ProviderToolCall(1, "c2", "lookup", '{ "n": 2 }', 3),
        ),
        text_items=(
            ProviderAssistantItem("a", 0, "before", "commentary"),
            ProviderAssistantItem("b", 2, "after", None),
        ),
    )
    history.append_provider_assistant(
        assistant,
        timestamp=datetime.now(UTC),
        run_id="r",
        item_event_uuids={"a": "event-a", "b": "event-b"},
    )
    for identity in ("c1", "c2"):
        history.append_tool_result(
            content="ok",
            tool_call_id=identity,
            tool_name="lookup",
            timestamp=datetime.now(UTC),
            event_uuid=identity,
            is_error=False,
            run_id="r",
        )
    wire = [item.to_openai() for item in history.items]
    SessionModelHistory.validate_provider_tool_history(wire)
    assert history.unmatched_tool_calls() == ()
    native = prepare_request(
        build_provider_spec(provider_id="deepseek", api_key="fixture"), wire, None, {}
    )
    assert [item["type"] for item in native["input"]] == [
        "message",
        "function_call",
        "message",
        "function_call",
        "function_call_output",
        "function_call_output",
    ]
    assert native["input"][0]["phase"] == "commentary"
    assert "phase" not in native["input"][2]
    chat = chat_replay_messages(wire)
    assert len(chat) == 3
    assert chat[0]["content"] == "beforeafter"
    assert len(chat[0]["tool_calls"]) == 2
    assert all("phase" not in item and "_assistant_response_id" not in item for item in chat)
    assert wire[0]["phase"] == "commentary"
    history.note_active_events(session)
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=SessionCacheEpoch(),
        boundary="assistant_completed",
        turn_terminal=True,
    )
    restored = restore_rollout(store.load(session.session_id))
    assert [item.to_openai() for item in restored.history.items] == wire
    assert [
        item.uuid for item in restored.history.items if item.content in {"before", "after"}
    ] == ["event-a", "event-b"]


@pytest.mark.parametrize("modes", [(True, False, True), (False, True, False)])
def test_run_snapshot_and_both_model_switch_directions(
    tmp_path: Path, modes: tuple[bool, bool, bool]
) -> None:
    clients = [
        NativeModel(script=[items()])
        if native
        else _ScriptedLLMClient(
            script=[[LLMTextDelta("legacy answer"), LLMTurnDone(stop_reason="end_turn")]]
        )
        for native in modes
    ]
    conv = _make_conversation(tmp_path, fake_llm=clients[0])
    for index, client in enumerate(clients):
        conv._llm_client = client
        stream = conv.stream(f"question {index}", run_id=f"run-{index}")
        # The snapshot is captured before its first status: replacing the
        # configured client during iteration cannot alter this run's semantics.
        next(stream)
        conv._llm_client = clients[(index + 1) % len(clients)]
        list(stream)
    loaded = JsonlSessionStore(tmp_path / "sessions").load(conv.session.session_id)
    actual = {
        event.meta["run_id"]: event.payload["mode"]
        for event in loaded.active_events
        if event.type == "assistant_phase"
    }
    assert actual == {
        f"run-{index}": "native" if native else "legacy" for index, native in enumerate(modes)
    }
    for native, client in zip(modes, clients, strict=True):
        assert ("可见消息阶段" in client.received[0][0]["content"]) == native
