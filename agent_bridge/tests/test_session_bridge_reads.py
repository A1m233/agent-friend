"""Lightweight IM polling and outbound reads never restore full session history."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from agent_bridge.session_bridge import SessionBridge

from agent import Event, JsonlSessionStore, Session, SessionManager


def test_polling_and_outbound_reads_use_presence_and_active_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("probe", "default", "test/model")
    store.create(session)
    user = Event(type="user_message", uuid="user", ts=session.created_at, payload={"content": "hi"})
    answer = Event(
        type="assistant_message", uuid="answer", ts=session.created_at, payload={"content": "hello"}
    )
    store.append_events(session.session_id, [user, answer])
    manager = SessionManager(store=store)
    runtime = MagicMock()
    runtime.persistent_session_manager = manager
    bridge = SessionBridge(runtime)

    def no_full_load(*args: object, **kwargs: object) -> Session:
        raise AssertionError("polling must not load full history")

    monkeypatch.setattr(manager, "open", no_full_load)
    monkeypatch.setattr(store, "load", no_full_load)
    assert bridge.session_exists(session.session_id)
    assert not bridge.session_exists("missing")
    assert bridge.persistent_model(session.session_id, default="fallback") == "test/model"
    assert bridge.persistent_model("missing", default="fallback") == "fallback"
    assert bridge.persistent_event(session.session_id, "answer") == answer
    assert bridge.assistant_events_after(session.session_id, "user") == (answer,)

    store.append_event(
        session.session_id,
        Event(
            type="turn_rewrite",
            uuid="rewrite",
            ts=session.created_at,
            payload={"inactive_event_uuids": ["answer"]},
        ),
    )
    assert bridge.persistent_event(session.session_id, "answer") is None
    assert bridge.assistant_events_after(session.session_id, "user") == ()
    store.delete(session.session_id)
    assert not bridge.session_exists(session.session_id)
