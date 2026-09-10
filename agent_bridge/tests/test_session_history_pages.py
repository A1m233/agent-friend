from pathlib import Path
from unittest.mock import MagicMock

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.meta import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import Event, JsonlSessionStore, Session, SessionManager


def test_history_page_route_preserves_full_read_and_rejects_rewritten_cursor(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("pages", "default", "test/model")
    store.create(session)
    for index in range(4):
        store.append_events(
            session.session_id,
            [
                Event(
                    type="user_message",
                    uuid=f"u{index}",
                    ts=session.created_at,
                    payload={"content": "hi"},
                ),
                Event(
                    type="assistant_message",
                    uuid=f"a{index}",
                    ts=session.created_at,
                    payload={"content": "ok"},
                ),
            ],
        )
    runtime = BridgeRuntime(
        settings=MagicMock(),
        persistent_store=store,
        transient_store=MagicMock(),
        persistent_session_manager=SessionManager(store=store),
        catalog=MagicMock(),
        tool_registry=MagicMock(),
        prompt_builder_factory=MagicMock(),
        default_persona="default",
        default_model="test/model",
    )
    app = FastAPI()
    register_routes(app, runtime)
    client = TestClient(app)
    url = f"/v1/sessions/{session.session_id}"
    page = client.get(url, params={"view": "active", "turn_limit": 2})
    assert page.status_code == 200
    assert [e["uuid"] for e in page.json()["active_events"]] == ["u2", "a2", "u3", "a3"]
    assert len(client.get(url).json()["active_events"]) == 9
    store.append_event(
        session.session_id,
        Event(
            type="turn_rewrite",
            uuid="rewrite",
            ts=session.created_at,
            payload={"inactive_event_uuids": ["u0", "a0"]},
        ),
    )
    assert (
        client.get(
            url,
            params={
                "view": "active",
                "turn_limit": 2,
                "before": page.json()["history_cursor"],
                "history_epoch": page.json()["history_epoch"],
            },
        ).status_code
        == 409
    )
