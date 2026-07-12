"""JsonlSessionStore persistence edge cases."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Thread
from uuid import uuid4

import portalocker
from agent.sessions import Event, JsonlSessionStore, Session


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
