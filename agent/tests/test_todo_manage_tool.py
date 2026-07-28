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
            "reminders": [
                {
                    "kind": "once",
                    "local_datetime": "2026-07-29T09:00:00",
                    "timezone": "Asia/Shanghai",
                }
            ],
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


def test_error_is_structured(tmp_path: Path) -> None:
    tool = TodoManageTool(TodoService(TodoStore(tmp_path / "todos.db")))
    result = tool.invoke({"operation": "get", "todo_id": "missing"})
    assert result.is_error
    assert result.meta["error_kind"] == "not_found"
    assert json.loads(result.text)["status"] == "error"
