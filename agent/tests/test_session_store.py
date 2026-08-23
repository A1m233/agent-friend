"""JsonlSessionStore persistence edge cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Thread
from uuid import uuid4

import portalocker
from agent.command_runtime.workspace import session_workspace_path
from agent.sessions import Event, EventType, JsonlSessionStore, Session, SessionManager


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


def test_session_manager_hard_delete_removes_managed_command_workspace(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    workspaces = tmp_path / "command-workspaces"
    store = JsonlSessionStore(sessions)
    session_id = "im:qq:OPENID-A"
    store.create(_session(session_id))
    workspace = session_workspace_path(workspaces, session_id)
    workspace.mkdir(parents=True)
    (workspace / "kept-across-archive.txt").write_text("content", encoding="utf-8")
    manager = SessionManager(store=store, command_workspaces_root=workspaces)

    manager.archive(session_id)
    assert workspace.exists()
    manager.delete(session_id)

    assert not workspace.exists()


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
