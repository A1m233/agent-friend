"""JsonlSessionStore persistence edge cases."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Thread
from uuid import uuid4

import portalocker
import pytest
from agent.command_runtime.workspace import session_workspace_path
from agent.sessions import (
    Event,
    EventType,
    JsonlSessionStore,
    NotifyingSessionStore,
    NullSessionStore,
    Session,
    SessionChange,
    SessionChangeBroker,
    SessionManager,
    SessionPersistError,
    SessionRevisionStore,
    ToolExecutionEvidence,
    ToolExecutionEvidenceStore,
)


def _session(session_id: str) -> Session:
    return Session.new(
        title="t",
        persona="default",
        model="m",
        session_id=session_id,
    )


def _user_event(text: str) -> Event:
    return Event(
        type="user_message",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={"content": text},
    )


def _event(event_type: EventType, ts: datetime) -> Event:
    payload = {"content": event_type} if event_type.endswith("_message") else {"to": "other"}
    return Event(
        type=event_type,
        uuid=str(uuid4()),
        ts=ts,
        payload=payload,
    )


def test_safe_session_id_keeps_legacy_raw_filename(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session = _session("plain-session-1")

    store.create(session)

    assert (tmp_path / "plain-session-1.jsonl").exists()
    assert store.load("plain-session-1").session_id == "plain-session-1"


@pytest.mark.parametrize("size", [17, 2_000_000])
def test_exists_does_not_read_history_or_take_a_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    store = JsonlSessionStore(tmp_path)
    # Presence is independent of content size, validity and readability.
    (tmp_path / "session-a.jsonl").write_bytes(b"x" * size)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("existence check must not load or read history")

    monkeypatch.setattr(store, "load", unexpected)
    monkeypatch.setattr(Path, "open", unexpected)
    monkeypatch.setattr(portalocker, "Lock", unexpected)
    monkeypatch.setattr(Event, "from_jsonl", unexpected)
    assert store.exists("session-a") is True
    assert store.exists("missing") is False


@pytest.mark.parametrize(
    "session_id", ["im:qq:OPENID-A", "../outside", "folder/nested", "C:\\outside"]
)
def test_exists_roundtrips_encoded_opaque_ids(tmp_path: Path, session_id: str) -> None:
    store = JsonlSessionStore(tmp_path)
    assert store.exists(session_id) is False
    store.create(_session(session_id))
    assert store.exists(session_id) is True
    assert store.load(session_id).session_id == session_id


@pytest.mark.parametrize(
    "session_id",
    ["../outside", "..\\outside", "/outside", "C:\\outside", "", ".", "..", "bad\x00id"],
)
def test_exists_never_checks_an_unsafe_raw_legacy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    (tmp_path / "outside.jsonl").write_bytes(b"private")

    def unexpected(_session_id: str) -> Path:
        raise AssertionError("unsafe id must use portable encoding only")

    monkeypatch.setattr(store, "_legacy_raw_path", unexpected)
    assert store.exists(session_id) is False


@pytest.mark.skipif(os.name == "nt", reason="Colon-containing raw legacy filenames are POSIX-only")
def test_exists_accepts_safe_legacy_component(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    (tmp_path / "im:qq:legacy.jsonl").write_bytes(b"corrupt legacy content")
    assert store.exists("im:qq:legacy") is True


def test_exists_rejects_non_file_and_preserves_attribute_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    (tmp_path / "directory.jsonl").mkdir()
    with pytest.raises(SessionPersistError, match="不是普通文件"):
        store.exists("directory")

    original_stat = Path.stat
    denied = PermissionError("attribute access denied")

    def stat_with_denial(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path.name == "denied.jsonl":
            raise denied
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat_with_denial)
    with pytest.raises(SessionPersistError, match="属性") as caught:
        store.exists("denied")
    assert caught.value.__cause__ is denied


def test_exists_rejects_resolved_path_outside_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    original_resolve = Path.resolve

    def resolve_outside(path: Path, strict: bool = False) -> Path:
        if path.name == "outside.jsonl":
            return tmp_path / "outside.jsonl"
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_outside)
    with pytest.raises(SessionPersistError, match="受管目录"):
        store.exists("outside")


def test_exists_normalizes_path_resolution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    failure = RuntimeError("symlink loop")

    def cannot_resolve(path: Path, strict: bool = False) -> Path:
        raise failure

    monkeypatch.setattr(Path, "resolve", cannot_resolve)
    with pytest.raises(SessionPersistError) as caught:
        store.exists("session-a")
    assert caught.value.__cause__ is failure


def test_exists_decorator_and_null_store_do_not_publish_changes(tmp_path: Path) -> None:
    inner = JsonlSessionStore(tmp_path / "sessions")
    inner.create(_session("session-a"))
    changes: list[SessionChange] = []
    broker = SessionChangeBroker(SessionRevisionStore(tmp_path / "revisions.json"), changes.append)
    store = NotifyingSessionStore(inner, broker)
    assert store.exists("session-a") is True
    assert store.exists("missing") is False
    assert NullSessionStore().exists("session-a") is False
    assert changes == []
    assert not (tmp_path / "revisions.json").exists()


def test_unsafe_session_id_uses_portable_filename_and_roundtrips(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session_id = "im:qq:OPENID-A"
    session = _session(session_id)

    store.create(session)
    store.append_event(session_id, _user_event("hello"))

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].name != f"{session_id}.jsonl"
    assert files[0].name.startswith("sid_")

    loaded = store.load(session_id)
    assert loaded.session_id == session_id
    assert [m.content for m in loaded.messages] == ["hello"]
    assert store.list()[0].session_id == session_id


def test_delete_unsafe_session_id_removes_encoded_file(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session_id = "im:qq:OPENID-A"

    store.create(_session(session_id))
    created = list(tmp_path.glob("*.jsonl"))
    assert len(created) == 1

    store.delete(session_id)

    assert not created[0].exists()
    assert list(tmp_path.glob("*.jsonl")) == []


def test_session_manager_hard_delete_removes_command_workspace_and_evidence(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    workspaces = tmp_path / "command-workspaces"
    store = JsonlSessionStore(sessions)
    session_id = "im:qq:OPENID-A"
    store.create(_session(session_id))
    workspace = session_workspace_path(workspaces, session_id)
    workspace.mkdir(parents=True)
    (workspace / "kept-across-archive.txt").write_text("content", encoding="utf-8")
    evidence_store = ToolExecutionEvidenceStore(tmp_path / "tool-evidence")
    evidence_ref = evidence_store.new_reference()
    evidence_store.write(
        ToolExecutionEvidence(
            evidence_ref=evidence_ref,
            session_id=session_id,
            run_id="run-1",
            tool_call_id="call-1",
            operation_id="proc-1",
            tool_name="exec_command",
            process_status="cancelled",
            output_seq=1,
            dropped_bytes=0,
            stdout="partial",
            captured_at=datetime.now(UTC).isoformat(),
        )
    )
    manager = SessionManager(
        store=store,
        command_workspaces_root=workspaces,
        tool_execution_evidence_store=evidence_store,
    )

    manager.archive(session_id)
    assert workspace.exists()
    assert evidence_store.load(session_id, evidence_ref) is not None
    manager.delete(session_id)

    assert not workspace.exists()
    assert evidence_store.load(session_id, evidence_ref) is None


def test_load_waits_for_in_progress_writer(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session = _session("session-a")
    store.create(session)
    path = tmp_path / "session-a.jsonl"
    load_started = ThreadEvent()
    load_finished = ThreadEvent()
    loaded: list[Session] = []

    def load_session() -> None:
        load_started.set()
        loaded.append(store.load("session-a"))
        load_finished.set()

    with portalocker.Lock(
        path,
        "a",
        timeout=1,
        flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
    ):
        thread = Thread(target=load_session)
        thread.start()
        assert load_started.wait(timeout=1)
        assert not load_finished.wait(timeout=0.05)

    thread.join(timeout=2)
    assert load_finished.is_set()
    assert loaded[0].session_id == "session-a"


def test_summary_activity_uses_last_conversation_message_not_control_event(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path)
    created_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    session = Session.new(
        title="t",
        persona="default",
        model="m",
        session_id="session-a",
        created_at=created_at,
    )
    store.create(session)
    message_at = created_at + timedelta(minutes=5)
    store.append_event("session-a", _event("user_message", message_at))
    store.append_event("session-a", _event("model_change", created_at + timedelta(hours=1)))

    assert store.list()[0].updated_at == message_at


def test_summary_projects_latest_atomic_model_runtime_config(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new(
        title="t",
        persona="default",
        model="kimi/kimi-k2.6",
        reasoning_preset="off",
        session_id="session-runtime-config",
    )
    store.create(session)
    store.append_event(
        session.session_id,
        Event(
            type="model_config_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "from": {"model": "kimi/kimi-k2.6", "reasoningPreset": "off"},
                "to": {
                    "model": "openrouter/openai/gpt-5.6-luna",
                    "reasoningPreset": "xhigh",
                },
            },
        ),
    )

    summary = store.list()[0]

    assert summary.model == "openrouter/openai/gpt-5.6-luna"
    assert summary.reasoning_preset == "xhigh"


def test_summary_activity_falls_back_to_created_at_and_sorts_by_latest_message(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path)
    created_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    empty = Session.new(
        title="empty",
        persona="default",
        model="m",
        session_id="empty",
        created_at=created_at,
    )
    active = Session.new(
        title="active",
        persona="default",
        model="m",
        session_id="active",
        created_at=created_at - timedelta(days=1),
    )
    store.create(empty)
    store.create(active)
    latest_message_at = created_at + timedelta(minutes=1)
    store.append_event("active", _event("assistant_message", latest_message_at))

    summaries = store.list()

    assert [summary.session_id for summary in summaries] == ["active", "empty"]
    assert summaries[0].updated_at == latest_message_at
    assert summaries[1].updated_at == created_at


def test_summary_reverse_scan_crosses_large_control_event_and_skips_partial_tail(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path)
    created_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    session = Session.new(
        title="t",
        persona="default",
        model="m",
        session_id="session-a",
        created_at=created_at,
    )
    store.create(session)
    message_at = created_at + timedelta(minutes=1)
    store.append_event("session-a", _event("user_message", message_at))
    store.append_event(
        "session-a",
        Event(
            type="tool_call_result",
            uuid=str(uuid4()),
            ts=created_at + timedelta(hours=1),
            payload={
                "tool_call_id": "call-1",
                "tool_name": "large-result",
                "content": "x" * 20_000,
                "is_error": False,
            },
        ),
    )
    with (tmp_path / "session-a.jsonl").open("ab") as file:
        file.write(b'{"incomplete":')

    assert store.list()[0].updated_at == message_at


@pytest.mark.parametrize(
    "old_model",
    [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
    ],
)
def test_removed_model_history_preserves_latest_selection_until_user_changes_it(
    tmp_path: Path,
    old_model: str,
) -> None:
    from llm_providers import ModelRuntimeConfig

    store = JsonlSessionStore(tmp_path)
    session = Session.new(
        title="old",
        persona="default",
        model=old_model,
        reasoning_preset="high",
        session_id="old-model",
    )
    store.create(session)
    # A later old-model selection must not fall back to the initial model either.
    target = "deepseek/deepseek-v4-flash-vision-exp"
    change = Event(
        type="model_config_change",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={"to": {"model": target, "reasoningPreset": "max"}},
    )
    store.append_event(session.session_id, change)
    assert store.load(session.session_id).current_model_config == ModelRuntimeConfig(target, "max")
    assert store.list()[0].model == target
    assert store.list()[0].reasoning_preset == "max"
    store.append_event(
        session.session_id,
        Event(
            type="model_config_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"to": {"model": "deepseek/deepseek-flash", "reasoningPreset": "high"}},
        ),
    )
    restored = store.load(session.session_id)
    assert restored.initial_model == old_model
    assert restored.current_model_config == ModelRuntimeConfig("deepseek/deepseek-flash", "high")
