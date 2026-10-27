from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from agent.scheduled_tasks import ScheduledTaskService, ScheduledTaskStore
from agent_bridge.routes.scheduled_tasks import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Catalog:
    def find_by_name(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(id="persona-id", name=name)


def test_notification_flag_roundtrip_and_strict_boolean(tmp_path: Path) -> None:
    client, service, _ = _client(tmp_path)
    body = {
        "target_session_id": "s",
        "name": "task",
        "instruction": "work",
        "schedule": {"kind": "fixed_interval", "interval": 1, "unit": "hour"},
        "notify_bound_im": True,
    }
    result = client.post("/v1/scheduled-tasks", json=body)
    assert result.status_code == 200
    task = result.json()
    assert task["notify_bound_im"] is True
    renamed = client.patch(
        f"/v1/scheduled-tasks/{task['id']}", json={"revision": task["revision"], "name": "renamed"}
    ).json()
    assert renamed["notify_bound_im"] is True
    run = client.post(f"/v1/scheduled-tasks/{task['id']}/runs").json()
    assert run["im_notification"]["state"] == "pending"
    changed = client.patch(
        f"/v1/scheduled-tasks/{task['id']}",
        json={"revision": renamed["revision"], "notify_bound_im": False},
    ).json()
    assert changed["notify_bound_im"] is False
    assert service.store.get_occurrence(run["id"]).task_snapshot.notify_bound_im
    assert (
        client.post("/v1/scheduled-tasks", json={**body, "notify_bound_im": "false"}).status_code
        == 422
    )


def _client(
    tmp_path: Path,
) -> tuple[TestClient, ScheduledTaskService, SimpleNamespace]:
    service = ScheduledTaskService(ScheduledTaskStore(tmp_path / "scheduled.db"))
    broker = SimpleNamespace(pending=lambda **_kwargs: ("pending",))
    runtime = SimpleNamespace(
        scheduled_task_service=service,
        session_request_service=None,
        skill_service=None,
        catalog=_Catalog(),
        default_persona="default",
        default_model="fake/model",
        approval_broker=broker,
    )
    app = FastAPI()
    register_routes(app, runtime)  # type: ignore[arg-type]
    return TestClient(app), service, broker


def test_scheduled_task_crud_and_history(tmp_path: Path) -> None:
    client, service, _broker = _client(tmp_path)
    created = client.post(
        "/v1/scheduled-tasks",
        json={
            "target_session_id": "session-1",
            "name": "日报",
            "instruction": "总结今天",
            "schedule": {
                "kind": "fixed_interval",
                "interval": 1,
                "unit": "hour",
            },
        },
    )
    assert created.status_code == 200
    task = created.json()

    listed = client.get("/v1/scheduled-tasks").json()["tasks"]
    assert [item["id"] for item in listed] == [task["id"]]

    paused = client.post(
        f"/v1/scheduled-tasks/{task['id']}/pause",
        json={"revision": task["revision"]},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(
        f"/v1/scheduled-tasks/{task['id']}/resume",
        json={"revision": paused.json()["revision"]},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    updated = client.patch(
        f"/v1/scheduled-tasks/{task['id']}",
        json={"revision": resumed.json()["revision"], "name": "更新后的日报"},
    )
    assert updated.status_code == 200
    assert client.get(f"/v1/scheduled-tasks/{task['id']}").json()["name"] == "更新后的日报"

    run = client.post(f"/v1/scheduled-tasks/{task['id']}/runs")
    assert run.status_code == 200
    occurrence = run.json()
    assert occurrence["waiting_for_approval"] is False
    service.store.claim_pending(datetime.now(UTC))
    history = client.get(f"/v1/scheduled-tasks/{task['id']}/occurrences").json()["occurrences"]
    assert [item["id"] for item in history] == [occurrence["id"]]
    assert history[0]["waiting_for_approval"] is True
    deleted = client.delete(
        f"/v1/scheduled-tasks/{task['id']}",
        params={"revision": updated.json()["revision"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"


def test_revision_conflict_is_409(tmp_path: Path) -> None:
    client, _service, _broker = _client(tmp_path)
    task = client.post(
        "/v1/scheduled-tasks",
        json={
            "target_session_id": "session-1",
            "name": "日报",
            "instruction": "总结今天",
            "schedule": {
                "kind": "fixed_interval",
                "interval": 1,
                "unit": "hour",
            },
        },
    ).json()
    response = client.patch(
        f"/v1/scheduled-tasks/{task['id']}",
        json={"revision": 99, "name": "新名字"},
    )
    assert response.status_code == 409
    assert response.json()["error_kind"] == "revision_conflict"


def test_schedule_dict_is_strictly_validated_at_rest_boundary(tmp_path: Path) -> None:
    client, _service, _broker = _client(tmp_path)
    response = client.post(
        "/v1/scheduled-tasks",
        json={
            "target_session_id": "session-1",
            "name": "非法周期",
            "instruction": "不应创建",
            "schedule": {
                "kind": "fixed_interval",
                "interval": True,
                "unit": "hour",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["error_kind"] == "invalid_schedule"
