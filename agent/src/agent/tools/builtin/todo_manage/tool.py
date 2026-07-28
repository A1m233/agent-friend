"""Agent 对统一 TodoService 的结构化写入与查询入口。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar

from agent.todos import (
    Reminder,
    TodoDetails,
    TodoError,
    TodoListQuery,
    TodoService,
    TodoStatus,
    schedule_from_dict,
    schedule_to_dict,
)

from ...protocol import ToolResult

_RUNTIME_SESSION_ID = "__agent_friend_current_session_id"


class TodoManageTool:
    name: ClassVar[str] = "todo_manage"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 10.0
    description: ClassVar[str] = (
        "创建、查询和管理用户的持久 Todo，以及一次性或基础周期 Reminder。"
        "用户要求记待办、列待办、修改、完成、取消、重新打开或设置提醒时必须使用；"
        "不能只在对话里口头答应。Todo 不提供时间时不要擅自添加 Reminder。"
        "更新必须使用 list/get 返回的稳定 id 和 revision，目标不唯一时先澄清。"
        "Reminder 必须提供明确 IANA timezone 和无歧义本地时间；工具返回失败时不得声称成功。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "create",
                    "list",
                    "get",
                    "update",
                    "complete",
                    "cancel",
                    "reopen",
                    "add_reminder",
                    "update_reminder",
                    "remove_reminder",
                ],
            },
            "todo_id": {"type": "string"},
            "reminder_id": {"type": "string"},
            "revision": {"type": "integer", "minimum": 1},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": ["open", "completed", "cancelled"]},
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
            "reminder": {"type": "object"},
            "reminders": {"type": "array", "items": {"type": "object"}},
            "enabled": {"type": "boolean"},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }

    def __init__(self, service: TodoService) -> None:
        self._service = service

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            payload = self._invoke(args)
        except TodoError as exc:
            return ToolResult(
                text=json.dumps(
                    {"status": "error", "error_kind": exc.kind, "message": exc.message},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                is_error=True,
                meta={"error_kind": exc.kind, **exc.details},
            )
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                text=json.dumps(
                    {
                        "status": "error",
                        "error_kind": "invalid_arguments",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                is_error=True,
                meta={"error_kind": "invalid_arguments"},
            )
        return ToolResult(
            text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            meta={"operation": args.get("operation")},
        )

    def _invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        operation = _required_string(args, "operation")
        if operation == "create":
            schedules = tuple(
                schedule_from_dict(_mapping(item, "reminders item"))
                for item in _list(args.get("reminders", []), "reminders")
            )
            created = self._service.create(
                title=_required_string(args, "title"),
                description=_optional_string(args, "description") or "",
                reminders=schedules,
                origin_kind="session" if args.get(_RUNTIME_SESSION_ID) else "agent",
                origin_id=_optional_string(args, _RUNTIME_SESSION_ID),
            )
            return _success(operation, todo=_details(created))
        if operation == "list":
            raw_statuses = _list(args.get("statuses", ["open"]), "statuses")
            statuses = tuple(TodoStatus(str(item)) for item in raw_statuses)
            listed = self._service.list(
                TodoListQuery(
                    statuses=statuses,
                    limit=_integer(args, "limit", default=100),
                    offset=_integer(args, "offset", default=0),
                )
            )
            return _success(operation, todos=[_details(item) for item in listed])

        todo_id = _required_string(args, "todo_id")
        if operation == "get":
            return _success(operation, todo=_details(self._service.get(todo_id)))
        revision = _integer(args, "revision")
        if operation == "update":
            updated = self._service.update(
                todo_id,
                revision=revision,
                title=_optional_string(args, "title"),
                description=_optional_string(args, "description"),
            )
            return _success(operation, todo=_details(updated))
        if operation == "complete":
            return _success(
                operation, todo=_details(self._service.complete(todo_id, revision=revision))
            )
        if operation == "cancel":
            return _success(
                operation, todo=_details(self._service.cancel(todo_id, revision=revision))
            )
        if operation == "reopen":
            return _success(
                operation, todo=_details(self._service.reopen(todo_id, revision=revision))
            )
        if operation == "add_reminder":
            schedule = schedule_from_dict(_mapping(args.get("reminder"), "reminder"))
            added_reminder = self._service.add_reminder(todo_id, schedule=schedule)
            return _success(operation, reminder=_reminder(added_reminder))

        reminder_id = _required_string(args, "reminder_id")
        if operation == "update_reminder":
            schedule = schedule_from_dict(_mapping(args.get("reminder"), "reminder"))
            updated_reminder = self._service.update_reminder(
                todo_id,
                reminder_id,
                revision=revision,
                schedule=schedule,
                enabled=bool(args.get("enabled", True)),
            )
            return _success(operation, reminder=_reminder(updated_reminder))
        if operation == "remove_reminder":
            removed_reminder = self._service.remove_reminder(
                todo_id, reminder_id, revision=revision
            )
            return _success(operation, reminder=_reminder(removed_reminder))
        raise TodoError("invalid_arguments", f"不支持的 operation: {operation}")


def _success(operation: str, **payload: Any) -> dict[str, Any]:
    return {"status": "success", "operation": operation, **payload}


def _details(value: TodoDetails) -> dict[str, Any]:
    todo = value.todo
    return {
        "id": todo.id,
        "title": todo.title,
        "description": todo.description,
        "status": todo.status.value,
        "revision": todo.revision,
        "created_at": _dt(todo.created_at),
        "updated_at": _dt(todo.updated_at),
        "completed_at": _dt(todo.completed_at),
        "cancelled_at": _dt(todo.cancelled_at),
        "reminders": [_reminder(item) for item in value.reminders],
    }


def _reminder(value: Reminder) -> dict[str, Any]:
    return {
        "id": value.id,
        "todo_id": value.todo_id,
        "kind": value.kind.value,
        "timezone": value.timezone,
        "schedule": schedule_to_dict(value.schedule),
        "next_fire_at_utc": _dt(value.next_fire_at_utc),
        "status": value.status.value,
        "fire_count": value.fire_count,
        "revision": value.revision,
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _required_string(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TodoError("invalid_arguments", f"{key} 必须是非空字符串")
    return value.strip()


def _optional_string(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TodoError("invalid_arguments", f"{key} 必须是字符串")
    return value


def _integer(args: dict[str, Any], key: str, *, default: int | None = None) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TodoError("invalid_arguments", f"{key} 必须是整数")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TodoError("invalid_arguments", f"{label} 必须是数组")
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TodoError("invalid_arguments", f"{label} 必须是对象")
    return value
