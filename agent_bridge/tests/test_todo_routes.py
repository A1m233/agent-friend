from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agent.todos import TodoService, TodoStore
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.todos import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path: Path) -> TestClient:
    service = TodoService(
        TodoStore(tmp_path / "todos.db"),
        clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC),
    )
    runtime = cast(BridgeRuntime, SimpleNamespace(todo_service=service))
    app = FastAPI()
    register_routes(app, runtime)
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
