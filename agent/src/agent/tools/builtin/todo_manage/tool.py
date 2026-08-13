"""Agent 对统一 TodoService 的结构化写入与查询入口。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.todos import (
    Reminder,
    ReminderSchedule,
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
_RUNTIME_NOW = "__agent_friend_runtime_now"
_BATCH_STATUS_OPERATIONS = frozenset({"complete", "cancel", "reopen"})
_MAX_BATCH_ITEMS = 50

_REMINDER_SCHEDULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "一次性或基础周期 Reminder 的结构化调度规则。kind=once 时必须且只能提供 "
        "local_datetime 或 relative_delay 之一；kind=recurring 时必须提供 frequency、"
        "local_time 和 starts_on。"
    ),
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["once", "recurring"],
            "description": "一次性提醒传 once，周期提醒传 recurring。",
        },
        "local_datetime": {
            "type": "string",
            "description": (
                "kind=once 时与 relative_delay 二选一。不带 UTC offset 的 ISO 8601 "
                "本地日期时间，例如 2026-08-04T09:30:00；时区单独通过 timezone 提供。"
            ),
        },
        "relative_delay": {
            "type": "object",
            "description": (
                "kind=once 时与 local_datetime 二选一；表示从当前可信时间起经过指定"
                "时长后提醒。最小单位为 minute。"
            ),
            "properties": {
                "amount": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "正整数时长。",
                },
                "unit": {
                    "type": "string",
                    "enum": ["minute", "hour", "day", "week"],
                    "description": "经过时长单位。",
                },
            },
            "required": ["amount", "unit"],
            "additionalProperties": False,
        },
        "frequency": {
            "type": "string",
            "enum": ["daily", "weekly", "monthly"],
            "description": "kind=recurring 时必填。",
        },
        "interval": {
            "type": "integer",
            "minimum": 1,
            "default": 1,
            "description": "每多少日、周或月触发一次。",
        },
        "local_time": {
            "type": "string",
            "pattern": "^([01]\\d|2[0-3]):[0-5]\\d:[0-5]\\d$",
            "description": "kind=recurring 时必填；格式 HH:MM:SS，例如 09:30:00。",
        },
        "timezone": {
            "type": "string",
            "description": "IANA 时区名称，例如 Asia/Shanghai。",
        },
        "starts_on": {
            "type": "string",
            "format": "date",
            "description": "kind=recurring 时必填；开始日期格式 YYYY-MM-DD。",
        },
        "weekdays": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 7},
            "uniqueItems": True,
            "description": "frequency=weekly 时必填；ISO 星期 1=周一，7=周日。",
        },
        "month_days": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 31},
            "uniqueItems": True,
            "description": "frequency=monthly 时必填；每月的日期。",
        },
        "until_local": {
            "type": "string",
            "description": "可选的无 UTC offset ISO 8601 本地结束时间。",
        },
        "max_occurrences": {
            "type": "integer",
            "minimum": 1,
            "description": "可选的最大触发次数。",
        },
        "fold": {
            "type": "integer",
            "enum": [0, 1],
            "default": 0,
            "description": "夏令时回拨导致本地时间重复时选择第 1 次（0）或第 2 次（1）。",
        },
    },
    "required": ["kind", "timezone"],
    "additionalProperties": False,
}


class TodoManageTool:
    name: ClassVar[str] = "todo_manage"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 10.0
    description: ClassVar[str] = (
        "创建、查询和管理用户的持久 Todo，以及一次性或基础周期 Reminder。"
        "用户要求记待办、列待办、修改、完成、取消、重新打开或设置提醒时必须使用；"
        "不能只在对话里口头答应。Reminder 时间可以来自当前请求，也可以来自能够唯一"
        "解析的相关上下文指代；能唯一确定时直接执行并用确定语气说明，不再追问。"
        "无法唯一确定而需要询问提醒时间时，本轮不得创建或修改 Reminder，等用户回答后"
        "再执行。新建 Todo 时提醒时间已经确定，必须在同一次 create 的 reminders 中一起提交；"
        "不能只创建无 Reminder 的 Todo 就声称提醒已设置。"
        "add_reminder 只用于给已经存在的 Todo 后补提醒。"
        "complete、cancel、reopen 必须把本次同一状态迁移涉及的全部 Todo 合并到一次 "
        "items 调用；单个 Todo 也传长度为 1 的 items。"
        "用户给出“几分钟/小时/天/周后”时，一次性提醒直接使用 relative_delay，"
        "不要自行猜测当前钟点或拆成第二次调用。"
        "更新必须使用 list/get 返回的稳定 id 和 revision，目标不唯一时先澄清。"
        "Reminder 必须提供明确 IANA timezone 和无歧义本地时间；工具返回失败时不得声称成功。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": (
                    "要执行的 Todo 或 Reminder 操作；各 operation 的必填字段由对应字段说明"
                    "定义，运行时会做二次校验。选择 create 时，如果当前请求或可唯一解析的"
                    "上下文已经确定提醒时间，reminders 就是本次调用的必填参数。"
                ),
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
            "todo_id": {
                "type": "string",
                "description": (
                    "get、update、add_reminder、update_reminder、remove_reminder 时必填的 "
                    "Todo 稳定 id。complete、cancel、reopen 改在 items 中提供。"
                ),
            },
            "reminder_id": {
                "type": "string",
                "description": "update_reminder、remove_reminder 时必填的 Reminder 稳定 id。",
            },
            "revision": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "update 使用 Todo revision；update_reminder、remove_reminder 使用 "
                    "Reminder revision。complete、cancel、reopen 改在 items 中提供；"
                    "create、list、get、add_reminder 不需要 revision。"
                ),
            },
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_BATCH_ITEMS,
                "description": (
                    "complete、cancel、reopen 必填。一次调用中要执行同一状态迁移的全部 "
                    "Todo；单项也使用长度为 1 的数组。todo_id 不得重复。"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "todo_id": {
                            "type": "string",
                            "description": "Todo 稳定 id。",
                        },
                        "revision": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "list/get 返回的 Todo revision。",
                        },
                    },
                    "required": ["todo_id", "revision"],
                    "additionalProperties": False,
                },
            },
            "title": {"type": "string", "description": "create 时必填的 Todo 标题。"},
            "description": {"type": "string", "description": "Todo 的可选详细说明。"},
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": ["open", "completed", "cancelled"]},
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
            "reminder": {
                **_REMINDER_SCHEDULE_SCHEMA,
                "description": (
                    "add_reminder 或 update_reminder 使用的一条调度规则；"
                    "新建 Todo 时请改用 reminders。"
                ),
            },
            "reminders": {
                "type": "array",
                "items": _REMINDER_SCHEDULE_SCHEMA,
                "minItems": 1,
                "description": (
                    "create 时携带的一条或多条提醒。提醒时间无论来自当前请求，还是来自能够"
                    "唯一解析的相关上下文，只要已经确定，就必须在同一次 create 调用中传入"
                    "本字段；不得省略，也不要先创建 Todo 再调用 add_reminder。"
                ),
            },
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
        status = payload.get("status")
        meta: dict[str, Any] = {"operation": args.get("operation")}
        if "items" in payload and isinstance(payload.get("items"), list):
            meta.update(
                {
                    "item_count": len(payload["items"]),
                    "success_count": payload.get("succeeded", 0),
                    "error_count": payload.get("failed", 0),
                }
            )
        return ToolResult(
            text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            is_error=status in {"partial", "failed"},
            meta=meta,
        )

    def _invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        operation = _required_string(args, "operation")
        if operation == "create":
            schedules = tuple(
                _schedule_from_input(_mapping(item, "reminders item"), args)
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

        if operation in _BATCH_STATUS_OPERATIONS:
            return self._invoke_status_batch(operation, args)

        todo_id = _required_string(args, "todo_id")
        if operation == "get":
            return _success(operation, todo=_details(self._service.get(todo_id)))
        if operation == "add_reminder":
            schedule = _schedule_from_input(_mapping(args.get("reminder"), "reminder"), args)
            added_reminder = self._service.add_reminder(todo_id, schedule=schedule)
            return _success(operation, reminder=_reminder(added_reminder))

        revision = _integer(args, "revision")
        if operation == "update":
            updated = self._service.update(
                todo_id,
                revision=revision,
                title=_optional_string(args, "title"),
                description=_optional_string(args, "description"),
            )
            return _success(operation, todo=_details(updated))
        reminder_id = _required_string(args, "reminder_id")
        if operation == "update_reminder":
            schedule = _schedule_from_input(_mapping(args.get("reminder"), "reminder"), args)
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

    def _invoke_status_batch(
        self,
        operation: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        items = _status_batch_items(args.get("items"))
        results: list[dict[str, Any]] = []
        succeeded = 0
        transition = getattr(self._service, operation)
        for index, (todo_id, revision) in enumerate(items):
            try:
                details = transition(todo_id, revision=revision)
            except TodoError as exc:
                error: dict[str, Any] = {
                    "index": index,
                    "status": "error",
                    "todo_id": todo_id,
                    "error_kind": exc.kind,
                    "message": exc.message,
                }
                if exc.details:
                    error["details"] = exc.details
                results.append(error)
                continue
            succeeded += 1
            results.append(
                {
                    "index": index,
                    "status": "success",
                    "todo_id": details.todo.id,
                    "todo_status": details.todo.status.value,
                    "revision": details.todo.revision,
                }
            )
        failed = len(results) - succeeded
        status = "success" if failed == 0 else "partial" if succeeded else "failed"
        return {
            "status": status,
            "operation": operation,
            "succeeded": succeeded,
            "failed": failed,
            "items": results,
        }


def _success(operation: str, **payload: Any) -> dict[str, Any]:
    return {"status": "success", "operation": operation, **payload}


def _status_batch_items(value: object) -> list[tuple[str, int]]:
    raw_items = _list(value, "items")
    if not 1 <= len(raw_items) <= _MAX_BATCH_ITEMS:
        raise TodoError(
            "invalid_arguments",
            f"items 必须包含 1-{_MAX_BATCH_ITEMS} 项",
        )
    parsed: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        item = _mapping(raw, f"items[{index}]")
        unknown = set(item) - {"todo_id", "revision"}
        if unknown:
            raise TodoError(
                "invalid_arguments",
                f"items[{index}] 包含未知字段: {', '.join(sorted(unknown))}",
            )
        todo_id = _required_string(item, "todo_id")
        revision = _integer(item, "revision")
        if revision < 1:
            raise TodoError("invalid_arguments", f"items[{index}].revision 必须大于等于 1")
        if todo_id in seen:
            raise TodoError("invalid_arguments", f"items 中 todo_id 不得重复: {todo_id}")
        seen.add(todo_id)
        parsed.append((todo_id, revision))
    return parsed


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


def _schedule_from_input(
    value: dict[str, Any],
    args: dict[str, Any],
) -> ReminderSchedule:
    """Normalize transient relative input to the existing persistent schedule model."""

    if value.get("kind") != "once":
        if "relative_delay" in value:
            raise TodoError("invalid_arguments", "relative_delay 只支持 kind=once")
        return schedule_from_dict(value)

    has_absolute = "local_datetime" in value
    has_relative = "relative_delay" in value
    if has_absolute == has_relative:
        raise TodoError(
            "invalid_arguments",
            "kind=once 必须且只能提供 local_datetime 或 relative_delay 之一",
        )
    if has_absolute:
        return schedule_from_dict(value)

    runtime_now = _trusted_runtime_now(args)
    timezone = _required_string(value, "timezone")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise TodoError("invalid_datetime", f"未知 IANA 时区: {timezone}") from exc

    relative = _mapping(value.get("relative_delay"), "relative_delay")
    amount = _integer(relative, "amount")
    if amount < 1:
        raise TodoError("invalid_arguments", "relative_delay.amount 必须大于或等于 1")
    unit = _required_string(relative, "unit")
    durations = {
        "minute": timedelta(minutes=amount),
        "hour": timedelta(hours=amount),
        "day": timedelta(days=amount),
        "week": timedelta(weeks=amount),
    }
    duration = durations.get(unit)
    if duration is None:
        raise TodoError(
            "invalid_arguments",
            "relative_delay.unit 只支持 minute、hour、day 或 week",
        )

    target_local = (runtime_now + duration).astimezone(zone)
    normalized = dict(value)
    normalized.pop("relative_delay", None)
    normalized["local_datetime"] = target_local.replace(tzinfo=None).isoformat()
    normalized["fold"] = target_local.fold
    return schedule_from_dict(normalized)


def _trusted_runtime_now(args: dict[str, Any]) -> datetime:
    raw = args.get(_RUNTIME_NOW)
    if not isinstance(raw, str) or not raw:
        raise TodoError(
            "invalid_datetime",
            "relative_delay 需要当前推理提供的可信运行时间",
        )
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TodoError("invalid_datetime", "可信运行时间格式无效") from exc
    if value.tzinfo is None:
        raise TodoError("invalid_datetime", "可信运行时间必须包含时区")
    return value
