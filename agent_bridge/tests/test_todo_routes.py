from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from agent.todos import DeliveryUpdate, OccurrenceState, TodoService, TodoStore
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.todos import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path: Path, *, owner_token: str | None = None) -> TestClient:
    service = TodoService(
        TodoStore(tmp_path / "todos.db"),
        clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC),
    )
    runtime = cast(BridgeRuntime, SimpleNamespace(todo_service=service))
    app = FastAPI()
    register_routes(app, runtime, owner_token=owner_token)
    return TestClient(app)


def test_todo_and_reminder_crud(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/v1/todos",
        json={
            "title": "提交材料",
            "reminders": [
                {
                    "kind": "once",
                    "local_datetime": "2026-07-29T09:00:00",
                    "timezone": "Asia/Shanghai",
                }
            ],
        },
    )
    assert created.status_code == 200
    todo = created.json()
    assert todo["reminders"][0]["nextFireAtUtc"] == "2026-07-29T01:00:00Z"

    listed = client.get("/v1/todos").json()["todos"]
    assert [item["id"] for item in listed] == [todo["id"]]
    patched = client.patch(
        f"/v1/todos/{todo['id']}",
        json={"revision": todo["revision"], "description": "带齐附件"},
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "带齐附件"
    completed = client.post(
        f"/v1/todos/{todo['id']}/complete",
        json={"revision": patched.json()["revision"]},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["reminders"][0]["status"] == "disabled"


def test_revision_conflict_maps_to_409(tmp_path: Path) -> None:
    client = _client(tmp_path)
    todo = client.post("/v1/todos", json={"title": "任务"}).json()
    client.patch(
        f"/v1/todos/{todo['id']}",
        json={"revision": 1, "description": "new"},
    )
    conflict = client.patch(
        f"/v1/todos/{todo['id']}",
        json={"revision": 1, "description": "stale"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "revision_conflict"


@pytest.mark.parametrize("state", list(OccurrenceState))
def test_reminder_times_survive_exhaustion_regardless_of_delivery(
    tmp_path: Path, state: OccurrenceState
) -> None:
    client = _client(tmp_path)
    todo = client.post(
        "/v1/todos",
        json={
            "title": "周期",
            "reminders": [
                {
                    "kind": "recurring",
                    "frequency": "daily",
                    "interval": 1,
                    "local_time": "09:00:00",
                    "timezone": "Asia/Shanghai",
                    "starts_on": "2026-07-29",
                    "max_occurrences": 2,
                }
            ],
        },
    ).json()
    store = TodoStore(tmp_path / "todos.db")
    for day in (29, 30):
        instant = datetime(2026, 7, day, 1, tzinfo=UTC)
        (occurrence_id,) = store.create_due_occurrences(instant)
        store.update_occurrence(occurrence_id, DeliveryUpdate(state=state), now=instant)
        detail = client.get(f"/v1/todos/{todo['id']}").json()
        listed = client.get("/v1/todos").json()["todos"][0]
        assert detail["reminders"] == listed["reminders"]
        reminder = detail["reminders"][0]
        assert reminder["lastOccurrenceAtUtc"] == f"2026-07-{day}T01:00:00Z"
        assert reminder["onceAtUtc"] is None
        assert reminder["nextFireAtUtc"] == ("2026-07-30T01:00:00Z" if day == 29 else None)
    assert reminder["status"] == "exhausted"
    # Closing invalidates pending delivery but must not erase the historical scheduled time.
    closed = client.post(f"/v1/todos/{todo['id']}/complete", json={"revision": 1}).json()
    assert closed["reminders"][0]["lastOccurrenceAtUtc"] == "2026-07-30T01:00:00Z"


def test_disabled_once_preserves_original_instant_without_an_occurrence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    todo = client.post(
        "/v1/todos",
        json={
            "title": "一次性",
            "reminders": [
                {
                    "kind": "once",
                    "local_datetime": "2026-07-29T09:00:00",
                    "timezone": "Asia/Shanghai",
                }
            ],
        },
    ).json()
    closed = client.post(f"/v1/todos/{todo['id']}/cancel", json={"revision": 1}).json()
    reminder = closed["reminders"][0]
    assert reminder["status"] == "disabled"
    assert reminder["nextFireAtUtc"] is None
    assert reminder["onceAtUtc"] == "2026-07-29T01:00:00Z"
    assert reminder["lastOccurrenceAtUtc"] is None


def test_public_writes_are_unknown_and_cannot_claim_actor(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rejected = client.post("/v1/todos", json={"title": "伪造", "actor": "user"})
    assert rejected.status_code == 422
    todo = client.post("/v1/todos", json={"title": "公共入口"}).json()
    assert todo["lastOperation"]["actor"] == "unknown"
    history = client.get(f"/v1/todos/{todo['id']}/history").json()
    assert history["items"][0]["actor"] == "unknown"
    assert history["items"][0]["after"]["title"] == "公共入口"
    assert client.get("/v1/todos", params={"search": "不存在"}).json() == {"todos": []}


def test_desktop_owner_channel_validates_credential_and_payload(tmp_path: Path) -> None:
    client = _client(tmp_path, owner_token="test-owner")
    endpoint = "/internal/todos/mutate"
    body = {"action": "create", "payload": {"title": "桌面创建"}}
    assert client.post(endpoint, json=body).status_code == 401
    assert (
        client.post(
            endpoint, json=body, headers={"X-Agent-Friend-Lifecycle-Token": "wrong"}
        ).status_code
        == 401
    )
    headers = {"X-Agent-Friend-Lifecycle-Token": "test-owner"}
    todo = client.post(endpoint, json=body, headers=headers).json()
    assert todo["lastOperation"]["actor"] == "user"
    assert (
        client.post(endpoint, json={**body, "actor": "agent"}, headers=headers).status_code == 422
    )
    assert client.post(endpoint, json={"action": "shutdown"}, headers=headers).status_code == 422
    updated = client.post(
        endpoint,
        headers=headers,
        json={
            "action": "update",
            "todo_id": todo["id"],
            "payload": {"revision": 1, "title": "人工编辑"},
        },
    )
    assert updated.status_code == 200
    conflict = client.post(
        endpoint,
        headers=headers,
        json={"action": "complete", "todo_id": todo["id"], "payload": {"revision": 1}},
    )
    assert conflict.status_code == 409
    done = client.post(
        endpoint,
        headers=headers,
        json={"action": "complete", "todo_id": todo["id"], "payload": {"revision": 2}},
    ).json()
    assert done["status"] == "completed"
    history = client.get(f"/v1/todos/{todo['id']}/history", params={"limit": 2}).json()
    assert [item["actor"] for item in history["items"]] == ["user", "user"]
    older = client.get(
        f"/v1/todos/{todo['id']}/history", params={"before_id": history["nextCursor"]}
    ).json()
    assert len(older["items"]) == 1


def test_desktop_reminders_and_create_failure_are_atomic(tmp_path: Path) -> None:
    client = _client(tmp_path, owner_token="test-owner")
    headers = {"X-Agent-Friend-Lifecycle-Token": "test-owner"}
    endpoint = "/internal/todos/mutate"
    schedule = {
        "kind": "once",
        "local_datetime": "2026-07-29T09:00:00",
        "timezone": "Asia/Shanghai",
    }
    failed = client.post(
        endpoint,
        headers=headers,
        json={
            "action": "create",
            "payload": {
                "title": "原子",
                "reminders": [schedule, {**schedule, "timezone": "invalid-zone"}],
            },
        },
    )
    assert failed.status_code == 422
    assert client.get("/v1/todos").json()["todos"] == []
    todo = client.post(
        endpoint,
        headers=headers,
        json={"action": "create", "payload": {"title": "原子", "reminders": [schedule]}},
    ).json()
    reminder = todo["reminders"][0]
    changed = client.post(
        endpoint,
        headers=headers,
        json={
            "action": "update_reminder",
            "todo_id": todo["id"],
            "reminder_id": reminder["id"],
            "payload": {
                "revision": reminder["revision"],
                "schedule": {**schedule, "local_datetime": "2026-07-30T09:00:00"},
            },
        },
    ).json()
    removed = client.post(
        endpoint,
        headers=headers,
        json={
            "action": "remove_reminder",
            "todo_id": todo["id"],
            "reminder_id": reminder["id"],
            "payload": {"revision": changed["revision"]},
        },
    ).json()
    assert removed["status"] == "disabled"
    assert client.get(f"/v1/todos/{todo['id']}").json()["lastOperation"]["actor"] == "user"
