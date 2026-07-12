from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.sessions.events import Event

from agent import (
    JsonlSessionStore,
    NotifyingSessionStore,
    Session,
    SessionChange,
    SessionChangeBroker,
    SessionNotFoundError,
    SessionRevisionStore,
    session_mutation_context,
)


def _session(session_id: str = "s1") -> Session:
    return Session.new("title", "default", "fake", session_id=session_id)


def test_notifying_store_revisions_cover_create_append_delete(tmp_path: Path) -> None:
    changes: list[SessionChange] = []
    broker = SessionChangeBroker(SessionRevisionStore(tmp_path / "revisions.json"), changes.append)
    store = NotifyingSessionStore(JsonlSessionStore(tmp_path / "sessions"), broker)
    session = _session()

    with session_mutation_context(origin="openai", channel="voice"):
        store.create(session)
        store.append_event(
            session.session_id,
            Event(type="user_message", uuid="u1", ts=session.created_at, payload={"content": "x"}),
        )
    store.delete(session.session_id)

    assert [change.revision for change in changes] == [1, 2, 3]
    assert [change.change_kind for change in changes] == ["created", "content", "deleted"]
    assert changes[0].origin == "openai"
    assert changes[0].channel == "voice"
    payload = json.loads((tmp_path / "revisions.json").read_text(encoding="utf-8"))
    assert payload["sessions"]["s1"] == {"deleted": True, "revision": 3}


def test_failed_business_write_does_not_advance_revision(tmp_path: Path) -> None:
    changes: list[SessionChange] = []
    store = NotifyingSessionStore(
        JsonlSessionStore(tmp_path / "sessions"),
        SessionChangeBroker(SessionRevisionStore(tmp_path / "revisions.json"), changes.append),
    )
    with pytest.raises(SessionNotFoundError):
        store.append_event(
            "missing",
            Event(
                type="user_message", uuid="u1", ts=_session().created_at, payload={"content": "x"}
            ),
        )
    assert changes == []
    assert not (tmp_path / "revisions.json").exists()


def test_revision_publish_failure_does_not_undo_business_write(tmp_path: Path) -> None:
    def broken(_change: object) -> None:
        raise RuntimeError("subscriber unavailable")

    store = NotifyingSessionStore(
        JsonlSessionStore(tmp_path / "sessions"),
        SessionChangeBroker(SessionRevisionStore(tmp_path / "revisions.json"), broken),
    )
    session = _session()
    store.create(session)
    assert store.load(session.session_id).session_id == session.session_id
