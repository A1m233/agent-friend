from pathlib import Path
from unittest.mock import MagicMock

import pytest
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.meta import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import Event, JsonlSessionStore, Session, SessionManager


def history_client(store: JsonlSessionStore) -> tuple[TestClient, BridgeRuntime]:
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
    return client, runtime


def test_history_page_route_preserves_full_read_and_rejects_rewritten_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    client, runtime = history_client(store)
    url = f"/v1/sessions/{session.session_id}"
    page = client.get(url, params={"view": "active", "turn_limit": 2})
    assert page.status_code == 200
    assert [e["uuid"] for e in page.json()["active_events"]] == ["u2", "a2", "u3", "a3"]
    assert len(client.get(url).json()["active_events"]) == 9
    # Diagnostic reads bypass execution restoration and the full UI projection.
    monkeypatch.setattr(
        runtime.persistent_session_manager,
        "open",
        MagicMock(side_effect=AssertionError("full load")),
    )
    diagnostic = client.get(f"{url}/diagnostics", params={"turn_limit": 2})
    assert diagnostic.status_code == 200
    assert [e["uuid"] for e in diagnostic.json()["events"]][1:] == ["u2", "a2", "u3", "a3"]
    assert "active_events" not in diagnostic.json()
    assert diagnostic.json()["coverage"]["includedTurns"] == 2
    assert client.get(f"{url}/diagnostics", params={"turn_limit": 0}).status_code == 422
    assert (
        client.get(f"{url}/diagnostics", params={"all_turns": True}).json()["coverage"][
            "includedTurns"
        ]
        == 4
    )
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


def test_original_active_route_reads_only_complete_page_and_removes_fragment_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("pages", "default", "test/model")
    store.create(session)
    for index in range(35):
        store.append_events(
            session.session_id,
            [
                Event(
                    type="user_message",
                    uuid=f"u{index}",
                    ts=session.created_at,
                    payload={"content": f"turn {index}"},
                ),
                Event(
                    type="tool_call_request",
                    uuid=f"call{index}",
                    ts=session.created_at,
                    payload={
                        "tool_name": "exec_command",
                        "tool_call_id": str(index),
                        "arguments_json": '{"cmd":"hello"}',
                    },
                ),
                Event(
                    type="tool_call_result",
                    uuid=f"result{index}",
                    ts=session.created_at,
                    payload={"tool_call_id": str(index), "content": "complete-result😀" * 20000},
                ),
                Event(
                    type="assistant_message",
                    uuid=f"a{index}",
                    ts=session.created_at,
                    payload={"content": "complete answer"},
                ),
            ],
        )
    store.query_page(session.session_id, turn_limit=30)
    fresh = JsonlSessionStore(tmp_path)
    client, runtime = history_client(fresh)
    fail = MagicMock(side_effect=AssertionError("unpaged read"))
    monkeypatch.setattr(fresh, "query", fail)
    monkeypatch.setattr(fresh, "_read_records", fail)
    monkeypatch.setattr(runtime.persistent_session_manager, "open", fail)
    url = f"/v1/sessions/{session.session_id}"
    page = client.get(url, params={"view": "active", "turn_limit": 30})
    assert page.status_code == 200
    body = page.json()
    assert [e["uuid"] for e in body["active_events"]][::4] == [f"u{i}" for i in range(5, 35)]
    assert body["active_events"][2]["payload"]["content"] == "complete-result😀" * 20000
    assert "history_items" not in body and "content_version" not in body
    assert all("body" not in e and "summary" not in e for e in body["active_events"])
    older = client.get(
        url,
        params={
            "view": "active",
            "turn_limit": 30,
            "before": body["history_cursor"],
            "history_epoch": body["history_epoch"],
        },
    )
    assert older.status_code == 200
    assert older.json()["history_cursor"] is None
    assert len(older.json()["active_events"]) == 21
    assert client.get(url + "/history").status_code == 404
    assert client.get(url + "/history/content").status_code == 404
    assert client.get(url, params={"turn_limit": 1}).status_code == 400
    fail.assert_not_called()
