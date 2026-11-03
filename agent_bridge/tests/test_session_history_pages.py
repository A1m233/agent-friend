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


@pytest.mark.parametrize("turn_limit", [None, 1])
def test_chat_history_omits_accounting_diagnostics_but_preserves_full_history(
    tmp_path: Path, turn_limit: int | None
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("diagnostics", "default", "test/model")
    store.create(session)
    accounting = {"messages": [{"diagnostic": "x" * 100_000}], "total_tokens": 12}
    event = Event(
        type="tool_call_request",
        uuid="call",
        ts=session.created_at,
        payload={"tool_name": "exec_command", "tool_call_id": "tool-1", "args": {"cmd": "echo hi"}},
        meta={"run_id": "run-1", "tool_run": {"status": "completed"}, "context_usage": accounting},
    )
    store.append_events(
        session.session_id,
        [
            Event(
                type="user_message",
                uuid="user",
                ts=session.created_at,
                payload={"content": "hello"},
            ),
            event,
            Event(
                type="tool_call_result",
                uuid="result",
                ts=session.created_at,
                payload={"tool_call_id": "tool-1", "content": "complete output", "is_error": False},
            ),
        ],
    )
    client, _ = history_client(store)
    url = f"/v1/sessions/{session.session_id}"
    params: dict[str, str | int] = {"view": "active"}
    if turn_limit is not None:
        params["turn_limit"] = turn_limit
    response = client.get(url, params=params)
    assert response.status_code == 200
    assert len(response.content) < 5_000
    events = {ev["uuid"]: ev for ev in response.json()["active_events"]}
    assert events["call"]["meta"] == {"run_id": "run-1", "tool_run": {"status": "completed"}}
    assert events["call"]["payload"] == event.payload
    assert events["result"]["payload"]["content"] == "complete output"
    full = client.get(url).json()
    for field in ("events", "active_events"):
        original = next(ev for ev in full[field] if ev["uuid"] == "call")
        assert original["meta"]["context_usage"] == accounting
    assert (
        next(ev for ev in store.load(session.session_id).events if ev.uuid == "call").meta
        == event.meta
    )


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
