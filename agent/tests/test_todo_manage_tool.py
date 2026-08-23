from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from agent.todos import TodoService, TodoStore
from agent.tools.builtin.todo_manage import TodoManageTool


def _payload(tool: TodoManageTool, args: dict[str, Any]) -> dict[str, Any]:
    result = tool.invoke(args)
    assert not result.is_error, result.text
    return cast(dict[str, Any], json.loads(result.text))


def _once_schedule(local_datetime: str = "2026-07-29T09:00:00") -> dict[str, Any]:
    return {
        "kind": "once",
        "local_datetime": local_datetime,
        "timezone": "Asia/Shanghai",
    }


def _relative_schedule(amount: int = 2, unit: str = "minute") -> dict[str, Any]:
    return {
        "kind": "once",
        "relative_delay": {"amount": amount, "unit": unit},
        "timezone": "Asia/Shanghai",
    }


def test_schema_exposes_create_with_reminders_and_operation_requirements() -> None:
    schema = TodoManageTool.parameters_schema
    assert "同一次 create" in TodoManageTool.description
    assert "相关上下文指代" in TodoManageTool.description
    assert "本轮不得创建或修改 Reminder" in TodoManageTool.description
    assert "不能只创建无 Reminder 的 Todo 就声称提醒已设置" in TodoManageTool.description
    assert len(schema["oneOf"]) == 10
    branches = {branch["properties"]["operation"]["const"]: branch for branch in schema["oneOf"]}
    assert branches["create"]["required"] == ["title"]
    assert branches["complete"]["required"] == ["items"]
    assert branches["update_reminder"]["required"] == [
        "todo_id",
        "reminder_id",
        "revision",
        "reminder",
    ]
    operation_description = cast(str, schema["properties"]["operation"]["description"])
    assert "reminders 就是本次调用的必填参数" in operation_description
    reminders_description = cast(str, schema["properties"]["reminders"]["description"])
    assert "相关上下文" in reminders_description
    assert "不得省略" in reminders_description
    assert "不要先创建 Todo 再调用 add_reminder" in reminders_description
    assert "create 时必填" in schema["properties"]["title"]["description"]
    assert "add_reminder 不需要 revision" in schema["properties"]["revision"]["description"]
    assert "add_reminder 或 update_reminder" in schema["properties"]["reminder"]["description"]
    assert "全部 Todo 合并到一次" in TodoManageTool.description
    items_schema = schema["properties"]["items"]
    assert items_schema["minItems"] == 1
    assert items_schema["maxItems"] == 50
    assert set(items_schema["items"]["required"]) == {"todo_id", "revision"}

    schedule_schema = schema["properties"]["reminders"]["items"]
    assert "oneOf" not in schedule_schema
    assert schedule_schema["properties"]["kind"]["enum"] == ["once", "recurring"]
    assert set(schedule_schema["required"]) == {"kind", "timezone"}
    assert "二选一" in schedule_schema["properties"]["local_datetime"]["description"]
    relative_schema = schedule_schema["properties"]["relative_delay"]
    assert relative_schema["properties"]["unit"]["enum"] == [
        "minute",
        "hour",
        "day",
        "week",
    ]
    for field in ("frequency", "local_time", "starts_on"):
        assert "kind=recurring 时必填" in schedule_schema["properties"][field]["description"]


def test_create_list_update_and_complete(tmp_path: Path) -> None:
    service = TodoService(
        TodoStore(tmp_path / "todos.db"),
        clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC),
    )
    tool = TodoManageTool(service)
    created = _payload(
        tool,
        {
            "operation": "create",
            "title": "提交材料",
            "reminders": [_once_schedule()],
            "__agent_friend_current_session_id": "session-1",
        },
    )["todo"]
    assert created["reminders"][0]["next_fire_at_utc"] == "2026-07-29T01:00:00Z"
    assert (
        service.store.get_reminder(created["reminders"][0]["id"]).origin_session_id == "session-1"
    )

    listed = _payload(tool, {"operation": "list"})["todos"]
    assert [item["id"] for item in listed] == [created["id"]]
    updated = _payload(
        tool,
        {
            "operation": "update",
            "todo_id": created["id"],
            "revision": created["revision"],
            "description": "带齐附件",
        },
    )["todo"]
    completed_batch = _payload(
        tool,
        {
            "operation": "complete",
            "items": [{"todo_id": created["id"], "revision": updated["revision"]}],
        },
    )
    completed = completed_batch["items"][0]
    assert completed == {
        "index": 0,
        "revision": updated["revision"] + 1,
        "status": "success",
        "todo_id": created["id"],
        "todo_status": "completed",
    }
    assert "description" not in completed
    assert "reminders" not in completed
    assert (
        _payload(tool, {"operation": "get", "todo_id": created["id"]})["todo"]["reminders"][0][
            "status"
        ]
        == "disabled"
    )


def test_complete_batch_handles_ten_todos_in_one_invoke(tmp_path: Path) -> None:
    service = TodoService(TodoStore(tmp_path / "todos.db"))
    tool = TodoManageTool(service)
    created = [
        _payload(tool, {"operation": "create", "title": f"Todo {index}"})["todo"]
        for index in range(10)
    ]

    result = tool.invoke(
        {
            "operation": "complete",
            "items": [{"todo_id": item["id"], "revision": item["revision"]} for item in created],
        }
    )

    assert result.is_error is False
    payload = json.loads(result.text)
    assert payload["status"] == "success"
    assert payload["succeeded"] == 10
    assert payload["failed"] == 0
    assert [item["todo_id"] for item in payload["items"]] == [item["id"] for item in created]
    assert all(item["todo_status"] == "completed" for item in payload["items"])
    assert result.meta == {
        "operation": "complete",
        "item_count": 10,
        "success_count": 10,
        "error_count": 0,
    }


def test_status_batch_reports_partial_revision_conflict_and_continues(tmp_path: Path) -> None:
    service = TodoService(TodoStore(tmp_path / "todos.db"))
    tool = TodoManageTool(service)
    created = [
        _payload(tool, {"operation": "create", "title": f"Todo {index}"})["todo"]
        for index in range(3)
    ]
    service.update(
        created[1]["id"],
        revision=created[1]["revision"],
        description="并发修改",
    )

    result = tool.invoke(
        {
            "operation": "complete",
            "items": [{"todo_id": item["id"], "revision": item["revision"]} for item in created],
        }
    )

    assert result.is_error is True
    payload = json.loads(result.text)
    assert payload["status"] == "partial"
    assert payload["succeeded"] == 2
    assert payload["failed"] == 1
    assert [item["status"] for item in payload["items"]] == [
        "success",
        "error",
        "success",
    ]
    conflict = payload["items"][1]
    assert conflict["error_kind"] == "revision_conflict"
    assert conflict["details"] == {"actual_revision": 2, "expected_revision": 1}
    assert service.get(created[2]["id"]).todo.status.value == "completed"


def test_status_batch_rejects_duplicate_ids_before_writing(tmp_path: Path) -> None:
    service = TodoService(TodoStore(tmp_path / "todos.db"))
    tool = TodoManageTool(service)
    created = _payload(tool, {"operation": "create", "title": "Todo"})["todo"]

    result = tool.invoke(
        {
            "operation": "complete",
            "items": [
                {"todo_id": created["id"], "revision": created["revision"]},
                {"todo_id": created["id"], "revision": created["revision"]},
            ],
        }
    )

    assert result.is_error is True
    assert json.loads(result.text)["error_kind"] == "invalid_arguments"
    assert service.get(created["id"]).todo.status.value == "open"


def test_status_transitions_require_items_while_update_remains_single(tmp_path: Path) -> None:
    service = TodoService(TodoStore(tmp_path / "todos.db"))
    tool = TodoManageTool(service)
    created = _payload(tool, {"operation": "create", "title": "Todo"})["todo"]
    updated = _payload(
        tool,
        {
            "operation": "update",
            "todo_id": created["id"],
            "revision": created["revision"],
            "description": "单项修改",
        },
    )["todo"]

    old_shape = tool.invoke(
        {
            "operation": "complete",
            "todo_id": updated["id"],
            "revision": updated["revision"],
        }
    )
    assert old_shape.is_error is True
    assert json.loads(old_shape.text)["error_kind"] == "invalid_arguments"

    cancelled = _payload(
        tool,
        {
            "operation": "cancel",
            "items": [{"todo_id": updated["id"], "revision": updated["revision"]}],
        },
    )["items"][0]
    reopened = _payload(
        tool,
        {
            "operation": "reopen",
            "items": [{"todo_id": updated["id"], "revision": cancelled["revision"]}],
        },
    )["items"][0]
    assert cancelled["todo_status"] == "cancelled"
    assert reopened["todo_status"] == "open"


def test_create_normalizes_relative_reminders_from_one_trusted_anchor(tmp_path: Path) -> None:
    service = TodoService(
        TodoStore(tmp_path / "todos.db"),
        clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC),
    )
    tool = TodoManageTool(service)

    created = _payload(
        tool,
        {
            "operation": "create",
            "title": "测试相对提醒",
            "reminders": [_relative_schedule(), _relative_schedule(1, "hour")],
            "__agent_friend_runtime_now": "2026-07-28T09:00:00+08:00",
        },
    )["todo"]

    normalized = {
        item["schedule"]["local_datetime"]: item["next_fire_at_utc"]
        for item in created["reminders"]
    }
    assert normalized == {
        "2026-07-28T09:02:00": "2026-07-28T01:02:00Z",
        "2026-07-28T10:00:00": "2026-07-28T02:00:00Z",
    }


def test_relative_delay_preserves_elapsed_time_across_dst_fallback(tmp_path: Path) -> None:
    service = TodoService(
        TodoStore(tmp_path / "todos.db"),
        clock=lambda: datetime(2026, 11, 1, 4, tzinfo=UTC),
    )
    tool = TodoManageTool(service)
    schedule = _relative_schedule(2, "hour")
    schedule["timezone"] = "America/New_York"

    created = _payload(
        tool,
        {
            "operation": "create",
            "title": "DST reminder",
            "reminders": [schedule],
            "__agent_friend_runtime_now": "2026-11-01T00:30:00-04:00",
        },
    )["todo"]

    reminder = created["reminders"][0]
    assert reminder["next_fire_at_utc"] == "2026-11-01T06:30:00Z"
    assert reminder["schedule"]["local_datetime"] == "2026-11-01T01:30:00"
    assert reminder["schedule"]["fold"] == 1


def test_relative_delay_requires_runtime_time_and_excludes_absolute_time(tmp_path: Path) -> None:
    tool = TodoManageTool(TodoService(TodoStore(tmp_path / "todos.db")))

    missing_clock = tool.invoke(
        {
            "operation": "create",
            "title": "测试",
            "reminders": [_relative_schedule()],
        }
    )
    assert missing_clock.is_error
    assert json.loads(missing_clock.text)["error_kind"] == "invalid_datetime"

    both = _relative_schedule()
    both["local_datetime"] = "2026-07-28T09:02:00"
    ambiguous = tool.invoke(
        {
            "operation": "create",
            "title": "测试",
            "reminders": [both],
            "__agent_friend_runtime_now": "2026-07-28T09:00:00+08:00",
        }
    )
    assert ambiguous.is_error
    assert "必须且只能提供" in json.loads(ambiguous.text)["message"]


def test_add_reminder_to_existing_todo_does_not_require_revision(tmp_path: Path) -> None:
    service = TodoService(
        TodoStore(tmp_path / "todos.db"),
        clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC),
    )
    tool = TodoManageTool(service)
    created = _payload(tool, {"operation": "create", "title": "提交材料"})["todo"]

    added = _payload(
        tool,
        {
            "operation": "add_reminder",
            "todo_id": created["id"],
            "reminder": _once_schedule(),
            "__agent_friend_current_session_id": "session-2",
        },
    )["reminder"]

    assert added["todo_id"] == created["id"]
    assert added["next_fire_at_utc"] == "2026-07-29T01:00:00Z"
    assert service.store.get_reminder(added["id"]).origin_session_id == "session-2"
    fetched = _payload(tool, {"operation": "get", "todo_id": created["id"]})["todo"]
    assert len(fetched["reminders"]) == 1


def test_invalid_reminder_shape_explains_expected_fields(tmp_path: Path) -> None:
    service = TodoService(TodoStore(tmp_path / "todos.db"))
    tool = TodoManageTool(service)

    result = tool.invoke(
        {
            "operation": "create",
            "title": "提交材料",
            "reminders": [{"datetime": "2026-07-29T09:00:00+08:00"}],
        }
    )

    assert result.is_error
    payload = json.loads(result.text)
    assert payload["error_kind"] == "invalid_recurrence"
    assert "kind=once" in payload["message"]
    assert "local_datetime" in payload["message"]
    assert service.list() == []


def test_error_is_structured(tmp_path: Path) -> None:
    tool = TodoManageTool(TodoService(TodoStore(tmp_path / "todos.db")))
    result = tool.invoke({"operation": "get", "todo_id": "missing"})
    assert result.is_error
    assert result.meta["error_kind"] == "not_found"
    assert json.loads(result.text)["status"] == "error"
