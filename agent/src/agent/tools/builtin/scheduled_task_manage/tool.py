"""Agent-facing CRUD and history entry for Scheduled Agent Tasks."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from ....scheduled_tasks import (
    ScheduledOccurrence,
    ScheduledOccurrenceOutcome,
    ScheduledTaskError,
    ScheduledTaskListQuery,
    ScheduledTaskService,
    ScheduledTaskStatus,
)
from ....scheduled_tasks.codec import occurrence_to_dict, schedule_from_input, task_to_dict
from ....skills import SkillCatalogService
from ...protocol import ToolResult

_RUNTIME_SESSION_ID = "__agent_friend_current_session_id"
_RUNTIME_NOW = "__agent_friend_runtime_now"

_SCHEDULE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "kind": {"const": "once", "description": "运行一次。"},
                "local_datetime": {
                    "type": "string",
                    "description": "不带 UTC offset 的本地 ISO 日期时间；与 relative_delay 二选一。",
                },
                "relative_delay": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer", "minimum": 1},
                        "unit": {"type": "string", "enum": ["minute", "hour", "day", "week"]},
                    },
                    "required": ["amount", "unit"],
                    "additionalProperties": False,
                    "description": "从当前可信时间起延迟多久；与 local_datetime 二选一。",
                },
                "timezone": {"type": "string", "description": "IANA 时区名称。"},
                "fold": {"type": "integer", "enum": [0, 1], "default": 0},
            },
            "required": ["kind", "timezone"],
            "oneOf": [{"required": ["local_datetime"]}, {"required": ["relative_delay"]}],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"const": "fixed_interval", "description": "按固定 duration 周期运行。"},
                "interval": {"type": "integer", "minimum": 1},
                "unit": {"type": "string", "enum": ["hour", "day", "week"]},
                "starts_at": {
                    "type": "string",
                    "description": "可选、包含 UTC offset 的 ISO 起始时间。",
                },
                "until_at": {
                    "type": "string",
                    "description": "可选、包含 UTC offset 的 ISO 截止时间。",
                },
                "max_occurrences": {"type": "integer", "minimum": 1},
            },
            "required": ["kind", "interval", "unit"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"const": "calendar", "description": "按本地日历规则周期运行。"},
                "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "interval": {"type": "integer", "minimum": 1, "default": 1},
                "local_time": {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d:[0-5]\\d$"},
                "timezone": {"type": "string", "description": "IANA 时区名称。"},
                "starts_on": {"type": "string", "format": "date"},
                "weekdays": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 7},
                    "uniqueItems": True,
                },
                "month_days": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 31},
                    "uniqueItems": True,
                },
                "until_local": {"type": "string"},
                "max_occurrences": {"type": "integer", "minimum": 1},
                "fold": {"type": "integer", "enum": [0, 1], "default": 0},
            },
            "required": ["kind", "frequency", "local_time", "timezone", "starts_on"],
            "additionalProperties": False,
        },
    ],
    "description": "一次性、至少每小时一次的固定周期，或日/周/月日历周期。",
}


class ScheduledTaskManageTool:
    name: ClassVar[str] = "scheduled_task_manage"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 10.0
    description: ClassVar[str] = (
        "创建、查询和管理用户的持久 Scheduled Agent Task，并查询每次 occurrence/run 历史。"
        "用户要求 Agent 在未来一次或周期性自动完成工作时必须使用本工具；普通 Todo/Reminder "
        "仍使用 todo_manage，短期当前会话轮询或 monitor 不使用本工具。创建成功后必须按工具"
        "返回值回显规范化时间、时区、周期和可选 Skill，不能只口头答应。更新、暂停、恢复和"
        "删除必须使用 list/get 返回的 task_id 与 revision；目标不唯一时先澄清。run_now 会立即"
        "创建同一历史中的 occurrence，但不会绕过同任务非重叠或现有权限。任务不会获得额外"
        "授权；工具失败时不得声称成功。"
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
                    "pause",
                    "resume",
                    "delete",
                    "run_now",
                    "history",
                ],
                "description": "要执行的 Scheduled Task 操作。",
            },
            "task_id": {
                "type": "string",
                "description": "list/create 以外操作使用的稳定 Scheduled Task id。",
            },
            "revision": {
                "type": "integer",
                "minimum": 1,
                "description": "修改操作必须使用 list/get 返回的最新 revision。",
            },
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
            "instruction": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20000,
                "description": "每次独立 Agent run 要完成的自包含用户指令。",
            },
            "schedule": _SCHEDULE_SCHEMA,
            "skill_id": {
                "type": "string",
                "minLength": 1,
                "description": "可选；只能复制当前 Skill catalog 的精确稳定 skill_id，不能传名称或路径。",
            },
            "clear_skill": {"type": "boolean", "description": "update 时移除已绑定 Skill。"},
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": ["active", "paused", "exhausted", "deleted"]},
                "uniqueItems": True,
                "description": "list 的状态过滤；省略时返回 active、paused 和 exhausted。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "list/history 的分页条数；默认分别为 100/50。",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "list/history 的零起始分页偏移；默认 0。",
            },
        },
        "required": ["operation"],
        "oneOf": [
            {
                "properties": {"operation": {"const": "create"}},
                "required": ["name", "instruction", "schedule"],
            },
            {"properties": {"operation": {"const": "list"}}},
            {"properties": {"operation": {"const": "get"}}, "required": ["task_id"]},
            {
                "properties": {"operation": {"const": "update"}},
                "required": ["task_id", "revision"],
                "not": {
                    "properties": {"clear_skill": {"const": True}},
                    "required": ["skill_id", "clear_skill"],
                },
                "anyOf": [
                    {"required": ["name"]},
                    {"required": ["instruction"]},
                    {"required": ["schedule"]},
                    {"required": ["skill_id"]},
                    {
                        "properties": {"clear_skill": {"const": True}},
                        "required": ["clear_skill"],
                    },
                ],
            },
            {"properties": {"operation": {"const": "pause"}}, "required": ["task_id", "revision"]},
            {"properties": {"operation": {"const": "resume"}}, "required": ["task_id", "revision"]},
            {"properties": {"operation": {"const": "delete"}}, "required": ["task_id", "revision"]},
            {"properties": {"operation": {"const": "run_now"}}, "required": ["task_id"]},
            {"properties": {"operation": {"const": "history"}}, "required": ["task_id"]},
        ],
        "additionalProperties": False,
    }

    def __init__(
        self,
        service: ScheduledTaskService,
        *,
        default_persona_id: str | None,
        default_persona_name: str,
        default_model_id: str,
        skill_service: SkillCatalogService | None = None,
        approval_pending: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._service = service
        self._default_persona_id = default_persona_id
        self._default_persona_name = default_persona_name
        self._default_model_id = default_model_id
        self._skill_service = skill_service
        self._approval_pending = approval_pending

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            payload = self._invoke(args)
        except ScheduledTaskError as exc:
            return ToolResult(
                text=json.dumps(
                    {
                        "status": "error",
                        "error_kind": exc.kind,
                        "message": exc.message,
                        **exc.details,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                is_error=True,
                meta={"error_kind": exc.kind, **exc.details},
            )
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                text=json.dumps(
                    {"status": "error", "error_kind": "invalid_arguments", "message": str(exc)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                is_error=True,
                meta={"error_kind": "invalid_arguments"},
            )
        meta: dict[str, Any] = {"operation": args.get("operation")}
        if isinstance(payload.get("error_kind"), str):
            meta["error_kind"] = payload["error_kind"]
        return ToolResult(
            text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            is_error=payload.get("status") == "error",
            meta=meta,
        )

    def _invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        operation = _required_string(args, "operation")
        if operation == "create":
            skill_id = _optional_string(args, "skill_id")
            self._validate_skill(skill_id)
            task = self._service.create(
                name=_required_string(args, "name"),
                instruction=_required_string(args, "instruction"),
                schedule=schedule_from_input(
                    _mapping(args.get("schedule"), "schedule"), now=_runtime_now(args)
                ),
                persona_id=self._default_persona_id,
                persona_name=self._default_persona_name,
                model_id=self._default_model_id,
                skill_id=skill_id,
                origin_session_id=_optional_string(args, _RUNTIME_SESSION_ID),
            )
            return {"status": "ok", "operation": operation, "task": task_to_dict(task)}
        if operation == "list":
            statuses = tuple(
                ScheduledTaskStatus(str(item))
                for item in _list(
                    args.get("statuses", ["active", "paused", "exhausted"]), "statuses"
                )
            )
            tasks = self._service.list(
                ScheduledTaskListQuery(
                    statuses=statuses,
                    limit=_integer(args, "limit", 100),
                    offset=_integer(args, "offset", 0),
                )
            )
            return {
                "status": "ok",
                "operation": operation,
                "tasks": [task_to_dict(item) for item in tasks],
            }
        task_id = _required_string(args, "task_id")
        if operation == "get":
            return {
                "status": "ok",
                "operation": operation,
                "task": task_to_dict(self._service.get(task_id)),
            }
        if operation == "update":
            skill_id = _optional_string(args, "skill_id")
            if skill_id is not None and args.get("clear_skill") is True:
                raise ValueError("skill_id 与 clear_skill=true 不能同时提供")
            self._validate_skill(skill_id)
            task = self._service.update(
                task_id,
                revision=_integer(args, "revision"),
                name=_optional_string(args, "name"),
                instruction=_optional_string(args, "instruction"),
                schedule=(
                    schedule_from_input(
                        _mapping(args["schedule"], "schedule"), now=_runtime_now(args)
                    )
                    if "schedule" in args
                    else None
                ),
                skill_id=skill_id,
                clear_skill=bool(args.get("clear_skill", False)),
            )
            return {"status": "ok", "operation": operation, "task": task_to_dict(task)}
        if operation in {"pause", "resume", "delete"}:
            task = getattr(self._service, operation)(task_id, revision=_integer(args, "revision"))
            return {"status": "ok", "operation": operation, "task": task_to_dict(task)}
        if operation == "run_now":
            occurrence = self._service.run_now(task_id)
            if occurrence.outcome is ScheduledOccurrenceOutcome.SKIPPED:
                return {
                    "status": "error",
                    "operation": operation,
                    "error_kind": occurrence.reason_code,
                    "message": "该任务已有非终态运行，本次立即运行已记录为 skipped。",
                    "occurrence": self._occurrence_payload(occurrence),
                }
            return {
                "status": "ok",
                "operation": operation,
                "occurrence": self._occurrence_payload(occurrence),
            }
        if operation == "history":
            items = self._service.history(
                task_id, limit=_integer(args, "limit", 50), offset=_integer(args, "offset", 0)
            )
            return {
                "status": "ok",
                "operation": operation,
                "occurrences": [self._occurrence_payload(item) for item in items],
            }
        raise ValueError(f"未知 operation: {operation}")

    def _validate_skill(self, skill_id: str | None) -> None:
        if skill_id is None:
            return
        if self._skill_service is None or skill_id not in self._skill_service.snapshot.by_id():
            raise ScheduledTaskError(
                "skill_not_found",
                "skill_id 不在当前可信 Skill catalog 中；请刷新 catalog 后使用精确 id。",
            )

    def _occurrence_payload(self, occurrence: ScheduledOccurrence) -> dict[str, Any]:
        waiting = False
        if (
            self._approval_pending is not None
            and occurrence.session_id is not None
            and occurrence.run_id is not None
        ):
            waiting = self._approval_pending(occurrence.session_id, occurrence.run_id)
        return occurrence_to_dict(occurrence, waiting_for_approval=waiting)


def _runtime_now(args: dict[str, Any]) -> datetime:
    value = args.get(_RUNTIME_NOW)
    if not isinstance(value, str):
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("可信当前时间必须包含时区")
    return parsed.astimezone(UTC)


def _required_string(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串")
    return value.strip()


def _optional_string(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是字符串")
    return value.strip() or None


def _mapping(value: object, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{key} 必须是 object")
    return dict(value)


def _list(value: object, key: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{key} 必须是 array")
    return value


def _integer(args: dict[str, Any], key: str, default: int | None = None) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} 必须是整数")
    return value
