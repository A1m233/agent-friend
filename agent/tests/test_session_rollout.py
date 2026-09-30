from __future__ import annotations

import copy
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from agent.messages import Message
from agent.sessions import (
    CacheEnvelopeState,
    Event,
    Session,
    SessionCacheEpoch,
    SessionModelContext,
    SessionModelHistory,
    SessionRolloutRecorder,
    SessionWriterConflictError,
    restore_rollout,
)
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from llm_providers.request_witness import witness_from_body

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionCorruptError,
    SessionManager,
    SessionPersistError,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderAssistantTurn,
    ProviderToolCall,
)
from memory import MemoryContext


@dataclass
class _RecordingLLM:
    replies: list[str] = field(default_factory=lambda: ["ok"])
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("stream expected")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(copy.deepcopy(request.messages))
        reply = self.replies[min(len(self.received) - 1, len(self.replies) - 1)]
        yield LLMTextDelta(text=reply)
        yield LLMTurnDone(
            stop_reason="end_turn",
            assistant_turn=ProviderAssistantTurn(content=reply),
        )


@dataclass
class _ToolLoopLLM:
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("stream expected")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(copy.deepcopy(request.messages))
        if len(self.received) == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="call-1",
                tool_name="counting_echo",
                args_json_delta='{"message":"hello"}',
            )
            yield LLMTurnDone(
                stop_reason="tool_use",
                assistant_turn=ProviderAssistantTurn(
                    content=None,
                    tool_calls=(
                        ProviderToolCall(
                            index=0,
                            tool_call_id="call-1",
                            tool_name="counting_echo",
                            arguments_json='{"message":"hello"}',
                        ),
                    ),
                ),
            )
            return
        yield LLMTextDelta(text="tool complete")
        yield LLMTurnDone(
            stop_reason="end_turn",
            assistant_turn=ProviderAssistantTurn(content="tool complete"),
        )


class _CountingEcho:
    name: ClassVar[str] = "counting_echo"
    description: ClassVar[str] = "Echo once."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.invocations = 0

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.invocations += 1
        return ToolResult(text=str(args["message"]))


class _BlockingCountingEcho(_CountingEcho):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.invocations += 1
        self.started.set()
        self.release.wait(timeout=2)
        self.completed.set()
        return ToolResult(text=str(args["message"]))


@dataclass
class _SequencedMemory:
    values: list[MemoryContext]

    def retrieve(self, *_args: Any, **_kwargs: Any) -> MemoryContext:
        if not self.values:
            return MemoryContext.empty()
        return self.values.pop(0)

    def observe(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _manager(
    root: Path,
    llm: _RecordingLLM | _ToolLoopLLM,
    *,
    memory: _SequencedMemory | None = None,
    registry: ToolRegistry | None = None,
    store: JsonlSessionStore | None = None,
    message_renderer: Any = None,
) -> SessionManager:
    catalog = PersonaCatalog(external_dir=root / "personas")
    return SessionManager(
        store=store or JsonlSessionStore(root / "sessions"),
        llm_client_factory=lambda _config: llm,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id,
            catalog=catalog,
        ),
        context_manager_factory=NaiveContextManager,
        memory=cast(Any, memory),
        tool_registry=registry,
        message_renderer=message_renderer,
    )


def _create(manager: SessionManager, *, title: str = "rollout") -> str:
    return manager.create(
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model="deepseek/deepseek-chat",
        title=title,
    ).session_id


def _run(manager: SessionManager, session_id: str, text: str) -> None:
    conversation = manager.start_conversation(manager.open(session_id))
    list(conversation.stream(text, surface=DESKTOP_CHAT_SURFACE))


def test_execution_load_keeps_business_events_and_verified_current_context(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    sid = _create(manager)
    _run(manager, sid, "first")
    _run(manager, sid, "second")
    manager.close()
    full = store.load(sid)
    expected = restore_rollout(full)

    compact = store.load_execution(sid)
    actual = compact.restored_rollout
    assert actual is not None
    assert actual.disposition == expected.disposition
    assert actual.commit == expected.commit
    assert compact.active_events == full.active_events
    assert all(not e.type.startswith("rollout_") for e in compact.events)
    assert not any(e.type == "model_transcript_item" for e in compact.events)

    reader = compact.journal_reader
    assert reader is not None
    assert list(reader()) == full.events
    next_manager = _manager(tmp_path, _RecordingLLM(), store=store)
    conversation = next_manager.start_conversation(compact)
    list(conversation.stream("third", surface=DESKTOP_CHAT_SURFACE))
    assert compact.restored_rollout is None
    assert not any(e.type == "rollout_commit" for e in conversation.session.events)
    next_manager.close()
    final = store.load_execution(sid)
    assert final.restored_rollout is not None
    assert final.restored_rollout.disposition == "exact"
    assert [e.payload["content"] for e in final.active_events if e.type == "user_message"] == [
        "first",
        "second",
        "third",
    ]


def test_compact_history_rewrite_recovers_same_verified_prefix(tmp_path: Path) -> None:
    from agent.sessions.turn_origin import history_before_rewrite

    store = JsonlSessionStore(tmp_path / "sessions")
    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    sid = _create(manager)
    _run(manager, sid, "first")
    _run(manager, sid, "second")
    manager.close()
    full, compact = store.load(sid), store.load_execution(sid)
    target = [e for e in full.active_events if e.type == "user_message"][-1]
    prefix = full.active_events[: full.active_events.index(target)]
    expected = history_before_rewrite(full, target.uuid, prefix)
    assert history_before_rewrite(compact, target.uuid, prefix) == expected


def test_execution_load_validates_corruption_after_rejected_commit(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    sid = _create(manager)
    _run(manager, sid, "first")
    manager.close()
    path = tmp_path / "sessions" / f"{sid}.jsonl"
    events = store.load(sid).events
    first_commit = next(e for e in events if e.type == "rollout_commit")
    first_commit.payload["base_commit_id"] = "invalid"
    path.write_text(
        "".join(e.to_jsonl() + "\n" for e in events) + "broken\n" + events[-1].to_jsonl() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionCorruptError, match="中间损坏"):
        store.load_execution(sid)


def test_idle_release_preserves_current_session_and_reopens_exact_history(tmp_path: Path) -> None:
    from agent.sessions.residency import SessionResidency

    store = JsonlSessionStore(tmp_path / "sessions")
    llm = _RecordingLLM()
    manager = _manager(tmp_path, llm, store=store)
    current, idle = _create(manager), _create(manager)
    _run(manager, current, "current question")
    _run(manager, idle, "idle question")
    policy = SessionResidency(max_bytes=1, max_entries=0)
    with policy.using(current):
        assert policy.sweep(
            manager.resident_states(),
            protected=lambda _: False,
            size=manager.resident_size,
            release=manager.release_idle_runtime,
        ) == (idle,)
        assert manager.runtime_state_diagnostics(current) is not None
        assert manager.runtime_state_diagnostics(idle) is None
        lease = JsonlSessionStore(store.base_dir).acquire_writer_lease(idle)
        lease.release()
        reopened = manager.open_for_execution(idle)
        assert reopened.restored_rollout is not None
        assert reopened.restored_rollout.disposition == "exact"
        conversation = manager.start_conversation(reopened)
        list(conversation.stream("after idle release", surface=DESKTOP_CHAT_SURFACE))
        assert any(message.get("content") == "idle question" for message in llm.received[-1])
    manager.close()


def test_warm_execution_never_loads_history_and_preserves_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _RecordingLLM()
    store = JsonlSessionStore(tmp_path / "sessions")
    manager = _manager(tmp_path, llm, store=store)
    sid = _create(manager)
    _run(manager, sid, "first")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("resident execution must not read full history")

    with monkeypatch.context() as patch:
        patch.setattr(store, "load", forbidden)
        for text in ("second", "third", "fourth"):
            session = manager.open_for_execution(sid)
            conv = manager.start_conversation(session)
            list(conv.stream(text, surface=DESKTOP_CHAT_SURFACE))
    manager.close()
    assert len(llm.received) == 4
    persisted = manager.open(sid)
    assert [e.payload["content"] for e in persisted.events if e.type == "user_message"] == [
        "first",
        "second",
        "third",
        "fourth",
    ]


def test_resident_execution_rejects_external_append(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _RecordingLLM())
    sid = _create(manager)
    _run(manager, sid, "first")
    path = tmp_path / "sessions" / f"{sid}.jsonl"
    with path.open("ab") as file:
        file.write(b"\n")
    try:
        with pytest.raises(SessionWriterConflictError, match="外部修改"):
            manager.open_for_execution(sid)
    finally:
        manager.close()


def test_deleted_resident_session_is_not_reported_as_creatable(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _RecordingLLM())
    sid = _create(manager)
    _run(manager, sid, "first")
    (tmp_path / "sessions" / f"{sid}.jsonl").unlink()
    try:
        with pytest.raises(SessionWriterConflictError, match="不能自动重建"):
            manager.open_for_execution(sid)
    finally:
        manager.close()


def test_cold_execution_rechecks_source_after_acquiring_writer(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    sid = _create(manager)
    stale = manager.open(sid)
    store.append_event(
        sid,
        Event(
            type="user_message",
            uuid="arrived-before-lease",
            ts=stale.created_at,
            payload={"content": "new history"},
        ),
    )
    try:
        manager.start_conversation(stale)
        assert any(e.uuid == "arrived-before-lease" for e in manager.open_for_execution(sid).events)
    finally:
        manager.close()


@pytest.mark.parametrize("corrupt_tail", [False, True])
def test_restore_serialized_result_is_isolated_from_source_and_other_restores(
    tmp_path: Path, corrupt_tail: bool
) -> None:
    manager = _manager(tmp_path, _RecordingLLM())
    sid = _create(manager)
    _run(manager, sid, "one")
    _run(manager, sid, "two")
    session = manager.open(sid)
    manager.close()
    if corrupt_tail:
        last = next(e for e in reversed(session.events) if e.type == "rollout_commit")
        session.events.append(
            Event(
                type="rollout_commit",
                uuid="invalid-tail",
                ts=last.ts,
                payload={**last.payload, "transcript_sha256": "bad"},
            )
        )
    first = restore_rollout(session)
    assert first.commit is not None
    before = copy.deepcopy(first.commit.serialized_messages)
    first.commit.serialized_messages[0]["content"] = "changed only by consumer"
    second = restore_rollout(session)
    assert second.commit is not None
    assert second.commit.serialized_messages == before
    assert first.disposition == second.disposition


def _create_legacy_session(store: JsonlSessionStore) -> Session:
    session = Session.new(
        title="legacy",
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model="deepseek/deepseek-chat",
    )
    store.create(session)
    now = datetime.now(UTC)
    store.append_events(
        session.session_id,
        [
            Event(
                type="user_message",
                uuid="legacy-user",
                ts=now,
                payload={"content": "legacy question"},
            ),
            Event(
                type="assistant_message",
                uuid="legacy-assistant",
                ts=now,
                payload={"content": "legacy answer", "partial": False},
            ),
        ],
    )
    return store.load(session.session_id)


def test_legacy_session_is_checkpointed_before_runtime_and_never_restored_from_business(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    legacy = _create_legacy_session(store)
    pending = restore_rollout(legacy)
    assert pending.disposition == "legacy_migration_pending"
    assert pending.reason == "legacy_migration"
    assert pending.migration_required is True

    llm = _RecordingLLM()
    first = _manager(tmp_path, llm, store=store)
    conversation = first.start_conversation(first.open(legacy.session_id))
    first_diagnostics = conversation.cache_epoch_diagnostics
    first_epoch_id = first_diagnostics["epoch_id"]

    assert llm.received == []
    assert first_diagnostics["recovery_disposition"] == "legacy_migrated"
    assert first_diagnostics["exact_process_continuation"] is False
    persisted = store.load(legacy.session_id)
    migration_commits = [
        event
        for event in persisted.events
        if event.type == "rollout_commit" and event.payload.get("boundary") == "legacy_migration"
    ]
    assert len(migration_commits) == 1
    assert migration_commits[0].payload["exact_eligible"] is False
    assert migration_commits[0].payload["exact_ineligibility_reason"] == "legacy_migration"
    first.close()

    second = _manager(tmp_path, _RecordingLLM(), store=store)
    restored_conversation = second.start_conversation(second.open(legacy.session_id))
    second_diagnostics = restored_conversation.cache_epoch_diagnostics
    assert second_diagnostics["recovery_disposition"] == "legacy_migrated"
    assert second_diagnostics["epoch_id"] == first_epoch_id
    assert [item.content for item in restored_conversation._session_model_history.items] == [
        "legacy question",
        "legacy answer",
    ]
    second.close()

    store.append_event(
        legacy.session_id,
        Event(
            type="user_message",
            uuid="business-only-suffix",
            ts=datetime.now(UTC),
            payload={"content": "must not enter restored rollout history"},
        ),
    )
    restored = restore_rollout(store.load(legacy.session_id))
    assert restored.reason == "business_history_changed"
    assert [item.content for item in restored.history.items] == [
        "legacy question",
        "legacy answer",
    ]


def test_legacy_migration_failure_releases_writer_and_does_not_open_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    legacy = _create_legacy_session(store)
    original_append_events = store.append_events

    def _fail_append(_session_id: str, _events: list[Event]) -> None:
        raise SessionPersistError("forced migration failure")

    monkeypatch.setattr(store, "append_events", _fail_append)
    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    with pytest.raises(SessionPersistError, match="forced migration failure"):
        manager.start_conversation(manager.open(legacy.session_id))
    assert manager.runtime_state_diagnostics(legacy.session_id) is None

    monkeypatch.setattr(store, "append_events", original_append_events)
    conversation = manager.start_conversation(manager.open(legacy.session_id))
    assert conversation.cache_epoch_diagnostics["recovery_disposition"] == "legacy_migrated"


def test_restart_restores_terminal_assistant_dynamic_context_and_old_memory_position(
    tmp_path: Path,
) -> None:
    first_llm = _RecordingLLM(["first answer"])
    first_memory = _SequencedMemory([MemoryContext(rendered="old-memory-recall", items=[])])
    first = _manager(tmp_path, first_llm, memory=first_memory)
    session_id = _create(first)
    _run(first, session_id, "first question")
    previous_wire = first_llm.received[-1]
    first.close()

    second_llm = _RecordingLLM(["second answer"])
    second = _manager(
        tmp_path,
        second_llm,
        memory=_SequencedMemory([MemoryContext.empty()]),
    )
    conversation = second.start_conversation(second.open(session_id))

    diagnostics = conversation.cache_epoch_diagnostics
    assert diagnostics["recovery_disposition"] == "exact"
    assert diagnostics["exact_process_continuation"] is True
    list(conversation.stream("second question", surface=DESKTOP_CHAT_SURFACE))

    current_wire = second_llm.received[0]
    assert current_wire[: len(previous_wire)] == previous_wire
    assert sum("old-memory-recall" in str(item.get("content")) for item in current_wire) == 1
    assistant_contents = [
        item.get("content") for item in current_wire if item.get("role") == "assistant"
    ]
    assert assistant_contents.count("first answer") == 1


def test_tool_transaction_restores_in_order_without_reinvoking_tool(tmp_path: Path) -> None:
    tool = _CountingEcho()
    registry = ToolRegistry([tool])
    first_llm = _ToolLoopLLM()
    first = _manager(tmp_path, first_llm, registry=registry)
    session_id = _create(first)
    _run(first, session_id, "use the tool")
    assert tool.invocations == 1
    previous_wire = first_llm.received[-1]
    first.close()

    restarted_llm = _RecordingLLM(["after restart"])
    restarted = _manager(tmp_path, restarted_llm, registry=registry)
    conversation = restarted.start_conversation(restarted.open(session_id))
    assert tool.invocations == 1
    list(conversation.stream("continue", surface=DESKTOP_CHAT_SURFACE))
    assert tool.invocations == 1
    assert restarted_llm.received[0][: len(previous_wire)] == previous_wire
    roles = [item.get("role") for item in restarted_llm.received[0]]
    assert roles.index("assistant") < roles.index("tool")


def test_cancelled_tool_result_and_turn_marker_restore_before_followup(tmp_path: Path) -> None:
    from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled

    tool = _BlockingCountingEcho()
    registry = ToolRegistry([tool])
    first_llm = _ToolLoopLLM()
    first = _manager(tmp_path, first_llm, registry=registry)
    session_id = _create(first)
    conversation = first.start_conversation(first.open(session_id))
    token = CancellationToken()
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            list(
                conversation.stream(
                    "use the tool",
                    run_id="cancelled-tool-run",
                    surface=DESKTOP_CHAT_SURFACE,
                    cancel_check=token.raise_if_cancelled,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    assert tool.started.wait(1)
    assert token.cancel(CancelReason.EXPLICIT_REQUEST)
    worker.join(timeout=1)
    tool.release.set()
    assert tool.completed.wait(1)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RunCancelled)
    first.close()

    restarted_llm = _RecordingLLM(["after restart"])
    restarted = _manager(tmp_path, restarted_llm, registry=registry)
    restored = restarted.start_conversation(restarted.open(session_id))
    assert restored.cache_epoch_diagnostics["recovery_disposition"] == "exact"
    list(restored.stream("continue", surface=DESKTOP_CHAT_SURFACE))

    assert tool.invocations == 1
    persisted = JsonlSessionStore(tmp_path / "sessions").load(session_id)
    results = [event for event in persisted.events if event.type == "tool_call_result"]
    assert len(results) == 1
    assert results[0].payload["executed"] == "unknown"
    followup = restarted_llm.received[0]
    assistant_index = next(index for index, item in enumerate(followup) if item.get("tool_calls"))
    assert followup[assistant_index + 1]["role"] == "tool"
    assert followup[assistant_index + 1]["tool_call_id"] == "call-1"
    assert any("<turn_aborted>" in str(item.get("content")) for item in followup)


def test_second_manager_cannot_write_same_resident_session(tmp_path: Path) -> None:
    first = _manager(tmp_path, _RecordingLLM())
    session_id = _create(first)
    first.start_conversation(first.open(session_id))

    second = _manager(tmp_path, _RecordingLLM())
    with pytest.raises(SessionWriterConflictError, match="另一个 Bridge"):
        second.start_conversation(second.open(session_id))

    first.close()
    conversation = second.start_conversation(second.open(session_id))
    assert conversation.current_model == "deepseek/deepseek-chat"


def test_same_manager_windows_share_one_resident_history_and_session(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _RecordingLLM())
    session_id = _create(manager)

    first = manager.start_conversation(manager.open(session_id))
    second = manager.start_conversation(manager.open(session_id))

    assert first.session is second.session
    assert first._session_model_history is second._session_model_history


def test_truncated_tail_recovers_last_commit_and_starts_new_epoch(tmp_path: Path) -> None:
    first = _manager(tmp_path, _RecordingLLM(["before crash"]))
    session_id = _create(first)
    _run(first, session_id, "first")
    first.close()

    path = tmp_path / "sessions" / f"{session_id}.jsonl"
    with path.open("ab") as file:
        file.write(b'{"type":"model_transcript_item","uuid":"partial')

    restarted_llm = _RecordingLLM(["after recovery"])
    restarted = _manager(tmp_path, restarted_llm)
    loaded = restarted.open(session_id)
    assert loaded.load_diagnostics.unhandled_tail_corruption is True
    conversation = restarted.start_conversation(loaded)
    diagnostics = conversation.cache_epoch_diagnostics
    assert diagnostics["recovery_disposition"] == "truncated_tail"
    assert diagnostics["reason"] == "truncated_tail"
    assert diagnostics["exact_process_continuation"] is False
    list(conversation.stream("continue", surface=DESKTOP_CHAT_SURFACE))
    restarted.close()

    reopened = _manager(tmp_path, _RecordingLLM())
    restored = reopened.start_conversation(reopened.open(session_id))
    assert restored.cache_epoch_diagnostics["recovery_disposition"] == "exact"


def test_accepted_but_unsent_user_event_is_preserved_once_and_not_auto_submitted(
    tmp_path: Path,
) -> None:
    first = _manager(tmp_path, _RecordingLLM(["terminal"]))
    session_id = _create(first)
    _run(first, session_id, "completed user")
    first.close()
    store = JsonlSessionStore(tmp_path / "sessions")
    store.append_event(
        session_id,
        Event(
            type="user_message",
            uuid="accepted-unsent-user",
            ts=datetime.now(UTC),
            payload={"content": "accepted but unsent"},
        ),
    )

    llm = _RecordingLLM()
    restarted = _manager(tmp_path, llm)
    conversation = restarted.start_conversation(restarted.open(session_id))

    assert llm.received == []
    assert conversation.cache_epoch_diagnostics["recovery_disposition"] == "interrupted"
    restored = restore_rollout(JsonlSessionStore(tmp_path / "sessions").load(session_id))
    assert [item.content for item in restored.history.items].count("accepted but unsent") == 0
    assert [
        item.content
        for item in JsonlSessionStore(tmp_path / "sessions").load(session_id).business_llm_messages
    ].count("accepted but unsent") == 1


def test_rollout_filters_credential_shaped_metadata_and_manifest_fields(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(
        title="credentials",
        persona="default",
        model="deepseek/deepseek-chat",
    )
    store.create(session)
    message = Message(
        role="assistant",
        content="safe answer",
        meta={"api_key": "sk-secret-value", "nested": {"authorization": "Bearer secret"}},
    )
    history = SessionModelHistory(
        items=[message],
        active_event_uuids=(session.session_id,),
    )
    epoch = SessionCacheEpoch(
        envelope=CacheEnvelopeState(
            persona_id="persona",
            system_prompt="safe system",
            tools="[]",
            settings=json.dumps({"api_key": "settings-secret", "temperature": 0}),
            model_route=json.dumps({"provider_id": "test"}),
        )
    )
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=epoch,
        boundary="assistant_completed",
        turn_terminal=True,
    )

    persisted = (tmp_path / "sessions" / f"{session.session_id}.jsonl").read_text(encoding="utf-8")
    assert "sk-secret-value" not in persisted
    assert "Bearer secret" not in persisted
    assert "settings-secret" not in persisted
    restored = restore_rollout(store.load(session.session_id))
    assert restored.exact_continuation_candidate is False
    assert restored.reason == "credential_redacted"


def test_terminal_commit_roundtrips_content_free_final_wire_witness(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="witness", persona="default", model="deepseek/deepseek-chat")
    store.create(session)
    history = SessionModelHistory(
        items=[Message(role="assistant", content="answer")],
        active_event_uuids=(session.session_id,),
    )
    witness = witness_from_body(
        json.dumps({"messages": [{"role": "user", "content": "question"}]}).encode(),
        provider_id="test-provider",
        transport="test-transport",
        transform_identity="test:v1",
    )
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.note_final_wire(
        witness,
        {
            "finished_at_unix_ms": 1,
            "cross_turn_lineage": {"input_tokens": 10, "response_model": "test-model"},
        },
    )
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=SessionCacheEpoch(),
        boundary="assistant_completed",
        turn_terminal=True,
    )

    restored = restore_rollout(store.load(session.session_id))
    assert restored.commit is not None
    assert restored.commit.final_wire_witness == witness
    assert restored.commit.final_wire_witness is not None
    assert restored.commit.final_wire_witness.transcript_item_digests


def test_hard_delete_removes_rollout_and_writer_lease_while_archive_keeps_it(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, _RecordingLLM(["answer"]))
    session_id = _create(manager)
    _run(manager, session_id, "hello")
    path = tmp_path / "sessions" / f"{session_id}.jsonl"
    lease_path = tmp_path / "sessions" / f".{session_id}.writer.lock"
    assert path.exists()
    assert lease_path.exists()

    manager.archive(session_id)
    assert path.exists()
    assert '"type": "rollout_commit"' in path.read_text(encoding="utf-8")

    manager.delete(session_id)
    assert not path.exists()
    assert not lease_path.exists()


def test_unknown_rollout_schema_blocks_writable_runtime_without_business_fallback(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="legacy", persona="default", model="deepseek/deepseek-chat")
    store.create(session)
    event = Event(
        type="rollout_segment",
        uuid="future-segment",
        ts=datetime.now(UTC),
        payload={"schema_version": 999},
    )
    store.append_event(session.session_id, event)
    restored = restore_rollout(store.load(session.session_id))
    assert restored.exact_continuation_candidate is False
    assert restored.reason == "incompatible_rollout_schema"
    assert restored.runtime_blocked is True
    assert restored.history.items == []

    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    with pytest.raises(SessionCorruptError, match="rollout 无有效提交点"):
        manager.start_conversation(manager.open(session.session_id))


def test_invalid_later_commit_recovers_previous_complete_commit(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="corrupt", persona="default", model="deepseek/deepseek-chat")
    store.create(session)
    history = SessionModelHistory(
        items=[Message(role="assistant", content="safe terminal")],
        active_event_uuids=(session.session_id,),
    )
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=SessionCacheEpoch(),
        boundary="assistant_completed",
        turn_terminal=True,
    )
    store.append_event(
        session.session_id,
        Event(
            type="rollout_commit",
            uuid="invalid-commit",
            ts=datetime.now(UTC),
            payload={
                "schema_version": 1,
                "base_commit_id": recorder.commit_id,
                "relation": "append",
                "item_event_uuids": [],
                "item_count": 999,
                "transcript_sha256": "invalid",
            },
        ),
    )

    restored = restore_rollout(store.load(session.session_id))
    assert restored.reason == "corrupt_committed_history"
    assert restored.exact_continuation_candidate is False
    assert [item.content for item in restored.history.items] == ["safe terminal"]


def test_global_new_chat_default_does_not_replace_restored_session_model(tmp_path: Path) -> None:
    first = _manager(tmp_path, _RecordingLLM(["answer"]))
    existing_id = _create(first)
    _run(first, existing_id, "hello")
    first.close()

    restarted = _manager(tmp_path, _RecordingLLM())
    existing = restarted.start_conversation(restarted.open(existing_id))
    new_id = restarted.create(
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model="kimi/kimi-k2.6",
        title="new default",
    ).session_id
    new_conversation = restarted.start_conversation(restarted.open(new_id))

    assert existing.current_model == "deepseek/deepseek-chat"
    assert existing.cache_epoch_diagnostics["recovery_disposition"] == "exact"
    assert new_conversation.current_model == "kimi/kimi-k2.6"


def test_explicit_model_switch_advances_only_target_session_epoch(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _RecordingLLM(["one", "two", "three"]))
    first_id = _create(manager, title="first")
    second_id = _create(manager, title="second")
    _run(manager, first_id, "first")
    _run(manager, second_id, "second")
    before_first = manager.runtime_state_diagnostics(first_id)
    before_second = manager.runtime_state_diagnostics(second_id)
    assert before_first is not None and before_second is not None
    before_first_generation = before_first["cacheEpochGeneration"]
    assert isinstance(before_first_generation, int)

    first = manager.start_conversation(manager.open(first_id))
    first.switch_model("kimi/kimi-k2.6")
    list(first.stream("after switch", surface=DESKTOP_CHAT_SURFACE))
    after_first = manager.runtime_state_diagnostics(first_id)
    after_second = manager.runtime_state_diagnostics(second_id)
    assert after_first is not None and after_second is not None

    assert after_first["cacheEpochGeneration"] == before_first_generation + 1
    assert after_first["cacheEpochId"] != before_first["cacheEpochId"]
    assert after_first["cacheEpochReason"] == "model_route_changed"
    assert after_second["cacheEpochGeneration"] == before_second["cacheEpochGeneration"]
    assert after_second["cacheEpochId"] == before_second["cacheEpochId"]


def test_rewrite_commit_replaces_old_history_and_preserves_compaction_epoch(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="rewrite", persona="default", model="deepseek/deepseek-chat")
    store.create(session)
    history = SessionModelHistory(
        items=[Message(role="user", content="old"), Message(role="assistant", content="old-a")],
        active_event_uuids=(session.session_id,),
    )
    epoch = SessionCacheEpoch()
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=epoch,
        boundary="assistant_completed",
        turn_terminal=True,
    )

    history.items = [
        Message(role="system", content="compacted summary"),
        Message(role="user", content="recent"),
    ]
    history.generation += 1
    history.last_relation = "rewrite"
    epoch.advance("compaction")
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=epoch,
        boundary="assistant_completed",
        turn_terminal=True,
    )

    restored = restore_rollout(store.load(session.session_id))
    assert restored.exact_continuation_candidate is True
    assert [item.content for item in restored.history.items] == ["compacted summary", "recent"]
    assert restored.cache_epoch.reason == "compaction"
    assert restored.history.generation == 1


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ({"model_route": "route-b"}, "model_route_changed"),
        ({"settings": {"temperature": 1}}, "request_settings_changed"),
        ({"tools": [{"name": "tool-b"}]}, "core_tools_changed"),
        ({"persona_id": "persona-b"}, "persona_changed"),
        ({"system_prompt": "system-b"}, "system_prompt_changed"),
        ({"history_rewrite_reason": "compaction"}, "compaction"),
    ],
)
def test_restored_epoch_detects_each_incompatible_identity(
    tmp_path: Path,
    change: dict[str, Any],
    expected_reason: str,
) -> None:
    store = JsonlSessionStore(tmp_path / expected_reason / "sessions")
    session = Session.new(title="identity", persona="default", model="deepseek/deepseek-chat")
    store.create(session)
    history = SessionModelHistory(
        items=[Message(role="assistant", content="answer")],
        active_event_uuids=(session.session_id,),
    )
    baseline: dict[str, Any] = {
        "persona_id": "persona-a",
        "system_prompt": "system-a",
        "tools": [{"name": "tool-a"}],
        "settings": {"temperature": 0},
        "model_route": "route-a",
    }
    epoch = SessionCacheEpoch()
    epoch.observe(**baseline)
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.commit(
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=epoch,
        boundary="assistant_completed",
        turn_terminal=True,
    )
    restored = restore_rollout(store.load(session.session_id))
    previous_epoch_id = restored.cache_epoch.epoch_id

    update = restored.cache_epoch.observe(**{**baseline, **change})

    assert update.changed is True
    assert update.reason == expected_reason
    assert restored.cache_epoch.epoch_id != previous_epoch_id


def test_image_tool_result_survives_rollout_restart_and_route_change(tmp_path: Path) -> None:
    import io
    from dataclasses import replace

    from agent.attachments import AttachmentStore
    from agent.image_reading import ImageReadingService
    from llm_providers.catalog import DEEPSEEK_VISION_CAPABILITIES
    from llm_providers.spec import ResponsesRoute
    from PIL import Image

    from llm_providers import LLMClient, MultimodalInputAdapter, ProviderSpec

    image_bytes = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(image_bytes, format="PNG")
    assets = AttachmentStore(tmp_path / "assets")
    image = (
        ImageReadingService(assets)
        .snapshot(image_bytes.getvalue(), display_name="sample.png")
        .image
    )
    spec = ProviderSpec(
        model="openai/test",
        api_key="test",
        context_window=128000,
        attachment_capabilities=DEEPSEEK_VISION_CAPABILITIES,
        responses=ResponsesRoute("test", "https://example.test", "deepseek"),
    )
    client = LLMClient(spec)
    adapter = MultimodalInputAdapter(open_asset=assets.open, cache_dir=tmp_path / "cache")

    class ImageEcho(_CountingEcho):
        def invoke(self, args: dict[str, Any]) -> ToolResult:
            self.invocations += 1
            return ToolResult("snapshot", images=(image,))

    tool = ImageEcho()
    first_llm = _ToolLoopLLM()
    manager = _manager(
        tmp_path,
        first_llm,
        registry=ToolRegistry([tool]),
        message_renderer=lambda m, _c: adapter.render_message(m, client),
    )
    session_id = _create(manager)
    _run(manager, session_id, "use the tool")
    sent = next(item for item in first_llm.received[-1] if item["role"] == "tool")
    assert sent["content"][1]["type"] == "image_url"
    assert tool.invocations == 1
    manager.close()

    second_llm = _RecordingLLM()
    unsupported = LLMClient(replace(spec, responses=None))
    second = _manager(
        tmp_path,
        second_llm,
        registry=ToolRegistry([tool]),
        message_renderer=lambda m, _c: adapter.render_message(m, unsupported),
    )
    conversation = second.start_conversation(second.open(session_id))
    assert any(item.images == (image,) for item in conversation._session_model_history.items)
    list(conversation.stream("continue", surface=DESKTOP_CHAT_SURFACE))
    old_result = next(item for item in second_llm.received[-1] if item["role"] == "tool")
    assert isinstance(old_result["content"], str) and "未提供" in old_result["content"]
    assert tool.invocations == 1
    assert any(item.images == (image,) for item in conversation._session_model_history.items)
    second.close()
