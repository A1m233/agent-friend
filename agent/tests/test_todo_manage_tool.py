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
    assert "不能只创建无 Reminder 的 Todo 就声称提醒已设置" in TodoManageTool.description
    assert "oneOf" not in schema
    assert "不要先创建 Todo 再调用 add_reminder" in cast(
        str, schema["properties"]["reminders"]["description"]
    )
    assert "create 时必填" in schema["properties"]["title"]["description"]
    assert "add_reminder 不需要 revision" in schema["properties"]["revision"]["description"]
    assert "add_reminder 或 update_reminder" in schema["properties"]["reminder"]["description"]

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
    completed = _payload(
        tool,
        {
            "operation": "complete",
            "todo_id": created["id"],
            "revision": updated["revision"],
        },
    )["todo"]
    assert completed["status"] == "completed"
    assert completed["reminders"][0]["status"] == "disabled"


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
        },
    )["reminder"]

    assert added["todo_id"] == created["id"]
    assert added["next_fire_at_utc"] == "2026-07-29T01:00:00Z"
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
