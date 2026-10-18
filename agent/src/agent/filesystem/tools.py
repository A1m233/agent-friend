"""Model-facing governed filesystem tools."""

from __future__ import annotations

import json
from typing import Any, ClassVar, cast

from ..tools.protocol import Tool, ToolResult
from .approvals import FilesystemApprovalBroker
from .models import (
    ApprovalAction,
    ApprovalItem,
    DestructiveAction,
    FilesystemAuthorizationLedger,
    FilesystemPermission,
    PendingFilesystemApproval,
)
from .policy import ProtectedPathPolicy, resolve_locator
from .service import FilesystemError, FilesystemService

FILESYSTEM_LEDGER_ARG = "__agent_friend_filesystem_ledger"
FILESYSTEM_SESSION_ID_ARG = "__agent_friend_current_session_id"
FILESYSTEM_RUN_ID_ARG = "__agent_friend_current_run_id"
FILESYSTEM_TOOL_CALL_ID_ARG = "__agent_friend_current_tool_call_id"
FILESYSTEM_SURFACE_ARG = "__agent_friend_interaction_surface"
FILESYSTEM_TOOL_NAMES = frozenset(
    {
        "request_filesystem_access",
        "filesystem_search",
        "filesystem_list_directory",
        "filesystem_stat",
        "filesystem_read_text",
        "filesystem_read_text_tail",
        "filesystem_write_text",
        "filesystem_apply_patch",
        "filesystem_create_directory",
        "filesystem_copy",
        "filesystem_move",
        "filesystem_delete",
    }
)
_PERMISSIONS = frozenset({"search_metadata", "read_text", "read_image", "create", "edit"})
_ACTIONS = frozenset({"overwrite", "move", "delete"})
_LOCATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "宿主解析的文件位置；kind 决定必须提供绝对 path 还是标准位置 name。",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["path", "standard"],
            "description": "绝对路径使用 path；宿主标准位置使用 standard。",
        },
        "path": {
            "type": "string",
            "description": "kind=path 时必填的绝对文件或目录路径。",
        },
        "name": {
            "type": "string",
            "enum": [
                "home",
                "desktop",
                "documents",
                "downloads",
                "agent_data",
                "agent_logs",
                "personal_skills",
                "shared_skills",
            ],
            "description": "kind=standard 时必填的宿主标准位置名称。",
        },
    },
    "required": ["kind"],
    "oneOf": [
        {"properties": {"kind": {"const": "path"}}, "required": ["path"]},
        {"properties": {"kind": {"const": "standard"}}, "required": ["name"]},
    ],
    "additionalProperties": False,
}


def _json_result(payload: dict[str, object], *, error: bool = False) -> ToolResult:
    error_kind = payload.get("error")
    meta: dict[str, object] = {"error_kind": error_kind} if error else {}
    if error and error_kind == "approval_surface_unavailable":
        meta["no_progress_family"] = error_kind
    elif error and error_kind == "permission_denied":
        permission_reason = payload.get("permission_reason")
        if isinstance(permission_reason, str) and permission_reason:
            meta["no_progress_family"] = f"filesystem_permission_denied:{permission_reason}"
    return ToolResult(
        text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        is_error=error,
        meta=meta,
    )


def _ledger(args: dict[str, Any]) -> FilesystemAuthorizationLedger:
    value = args.get(FILESYSTEM_LEDGER_ARG)
    if not isinstance(value, FilesystemAuthorizationLedger):
        raise FilesystemError(
            "permission_denied",
            "当前用户轮没有文件授权上下文。",
            permission_reason="authorization_context_unavailable",
        )
    return value


def _permission_recovery_fields() -> dict[str, object]:
    return {
        "retryable": False,
        "retry_after": "new_current_turn_filesystem_scope",
        "required_action": "use_current_turn_scope_or_request_filesystem_access",
        "scope_lifetime": "current_user_turn",
    }


class RequestFilesystemAccessTool:
    name: ClassVar[str] = "request_filesystem_access"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 310.0
    result_context_retention: ClassVar[str] = "pinned"
    description: ClassVar[str] = (
        "请求当前用户轮内的最小本地文件权限。普通会话默认没有任何用户文件权限；"
        "已知位置时请求准确文件或最小目录，不知道位置时先对合理的标准位置请求"
        "search_metadata。search_metadata 只查名称/路径，read_text 读取已有文本，read_image 读取图片，二者不互相授权；"
        "目录级 read_text 不隐含列目录权限；文件名未知、需要先列出目录再读取时，"
        "必须在同一个目录 item 中同时申请 search_metadata 和 read_text，避免重复授权。"
        "create 用于新建文件、目录、备份及复制/移动目的地，edit 只修改已有目标且不"
        "包含 create。对 .git/.cursor/.codex/.claude 等配置目录只能申请目录级"
        "search_metadata；读取或修改正文必须改为申请准确文件。每次调用都提交精确"
        "请求；宿主可能按当前会话设置自动给出仅对该请求有效的一次性决定，不能据此"
        "扩大后续范围。覆盖、移动、删除必须在 actions 中列出准确目标。关键根递归删除"
        "或目标无法确定的强制递归删除仍受独立灾难性策略约束；若返回"
        "catastrophic_action_blocked_in_session_auto，不要原样重试或改写目标绕过，"
        "应请用户切回每次授权并重新检查准确请求。仅桌面聊天和宿主已登记的 QQ 私聊运行"
        "支持审批；其他入口会明确拒绝，不能假设"
        "获得权限。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": "展示给用户的简短授权理由，说明当前任务为什么需要这些准确范围。",
            },
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "description": "需要授权的 1-8 个准确文件或最小目录范围。",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                            "description": "本次授权请求内由 Agent 自定的唯一项目标签；不是 capability ID。",
                        },
                        "locator": _LOCATOR_SCHEMA,
                        "root_kind": {
                            "type": "string",
                            "enum": ["file", "directory"],
                            "description": "申请目标是准确文件还是最小目录。",
                        },
                        "permissions": {
                            "type": "array",
                            "minItems": 1,
                            "description": (
                                "search_metadata 只查名称/路径；read_text 读取已有文本；read_image 读取图片；"
                                "目录内文件名未知且要先列出再读取时，两者必须同时申请；"
                                "read_text 单独申请不会授权 filesystem_list_directory；"
                                "create 新建文件、目录、备份或复制/移动目的地；edit 只修改"
                                "已有目标，不能创建缺失目标。"
                            ),
                            "items": {
                                "type": "string",
                                "enum": [
                                    "search_metadata",
                                    "read_text",
                                    "read_image",
                                    "create",
                                    "edit",
                                ],
                            },
                            "uniqueItems": True,
                        },
                    },
                    "required": ["item_id", "locator", "root_kind", "permissions"],
                    "additionalProperties": False,
                },
            },
            "actions": {
                "type": "array",
                "maxItems": 16,
                "description": "本轮随后可能执行、需要用户单独确认的准确破坏性动作。",
                "items": {
                    "type": "object",
                    "properties": {
                        "action_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                            "description": (
                                "本次请求内由 Agent 自定的唯一破坏性动作标签；批准结果会原样返回，"
                                "后续只能用于匹配的 overwrite/move/delete 参数。"
                            ),
                        },
                        "action": {
                            "type": "string",
                            "enum": ["overwrite", "move", "delete"],
                            "description": "需要用户单独确认的破坏性动作类型。",
                        },
                        "locator": _LOCATOR_SCHEMA,
                        "recursive": {
                            "type": "boolean",
                            "default": False,
                            "description": "仅目录删除使用；必须与后续删除调用保持一致。",
                        },
                    },
                    "required": ["action_id", "action", "locator"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["reason", "items"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        broker: FilesystemApprovalBroker | None,
        policy: ProtectedPathPolicy,
    ) -> None:
        self._broker = broker
        self._policy = policy

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        surface = str(args.get(FILESYSTEM_SURFACE_ARG) or "")
        if self._broker is None or not self._broker.surface_available(
            str(args.get(FILESYSTEM_SESSION_ID_ARG) or ""),
            str(args.get(FILESYSTEM_RUN_ID_ARG) or ""),
            surface,
        ):
            return _json_result(
                {
                    "status": "unavailable",
                    "error": "approval_surface_unavailable",
                    "message": "当前入口不能展示本地文件授权，请在桌面聊天中继续。",
                },
                error=True,
            )
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
            return _json_result(
                {"status": "denied", "error": "invalid_arguments", "message": "reason 无效。"},
                error=True,
            )
        try:
            items = self._parse_items(args.get("items"))
            actions = self._parse_actions(args.get("actions", []))
            ledger = _ledger(args)
        except (ValueError, FilesystemError) as exc:
            return _json_result(
                {
                    "status": "denied",
                    "error": getattr(exc, "code", "invalid_arguments"),
                    "message": str(exc),
                },
                error=True,
            )
        session_id = str(args.get(FILESYSTEM_SESSION_ID_ARG) or "")
        run_id = str(args.get(FILESYSTEM_RUN_ID_ARG) or "")
        tool_call_id = str(args.get(FILESYSTEM_TOOL_CALL_ID_ARG) or "")
        request = PendingFilesystemApproval.create(
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            reason=reason.strip(),
            items=items,
            actions=actions,
        )
        decision = self._broker.request(request, timeout_seconds=300.0, surface=surface)
        if decision is None:
            return _json_result(
                {
                    "status": "expired",
                    "request_id": request.request_id,
                    "error": "approval_expired",
                    "message": "文件授权请求已超时，未执行任何文件操作。",
                },
                error=True,
            )
        if request.status == "cancelled":
            return _json_result(
                {
                    "status": "cancelled",
                    "request_id": request.request_id,
                    "error": "approval_cancelled",
                    "message": "文件授权请求已随任务取消，未执行任何文件操作。",
                },
                error=True,
            )
        if request.decision_source == "catastrophic_policy":
            risk = request.risk_assessment
            return _json_result(
                {
                    "status": "denied",
                    "request_id": request.request_id,
                    "error": "catastrophic_action_blocked_in_session_auto",
                    "risk_code": risk.code if risk is not None else None,
                    "message": (
                        "该动作不会在本会话无需授权模式下执行。"
                        "请让用户切回每次授权后重新检查并发起精确请求。"
                    ),
                    "retryable_without_user_change": False,
                    "approval": _approval_audit(request),
                },
                error=True,
            )
        scopes: list[dict[str, object]] = []
        denied_item_ids: list[str] = []
        for item in items:
            approved = decision.approved_permissions.get(item.item_id, frozenset())
            permissions = frozenset(
                permission for permission in approved if permission in item.permissions
            )
            if permissions:
                scope = ledger.grant_scope(
                    root=item.path,
                    root_kind=item.root_kind,
                    permissions=permissions,
                    allow_protected=item.allow_protected,
                )
                scopes.append(
                    {
                        "item_id": item.item_id,
                        "filesystem_scope_id": scope.scope_id,
                        "permissions": sorted(scope.permissions),
                    }
                )
            if permissions != item.permissions:
                denied_item_ids.append(item.item_id)
        granted_actions: list[dict[str, str]] = []
        for action in actions:
            if action.action_id in decision.approved_action_ids:
                ledger.grant_action(
                    action,
                    risk_fingerprint=(
                        request.risk_assessment.fingerprint
                        if request.risk_assessment is not None
                        else None
                    ),
                )
                granted_actions.append({"action_id": action.action_id, "item_id": action.action_id})
        status = (
            "granted"
            if not denied_item_ids and len(granted_actions) == len(actions)
            else "partially_granted"
            if scopes or granted_actions
            else "denied"
        )
        return _json_result(
            {
                "status": status,
                "request_id": request.request_id,
                "scopes": scopes,
                "actions": granted_actions,
                "denied_item_ids": denied_item_ids,
                "approval": _approval_audit(request),
            },
            error=status == "denied",
        )

    def _parse_items(self, raw: object) -> tuple[ApprovalItem, ...]:
        if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
            raise ValueError("items 必须包含 1-8 项")
        seen: set[str] = set()
        parsed: list[ApprovalItem] = []
        for value in raw:
            if not isinstance(value, dict):
                raise ValueError("items 项必须是对象")
            item_id = value.get("item_id")
            root_kind = value.get("root_kind")
            permissions = value.get("permissions")
            if not isinstance(item_id, str) or not item_id or item_id in seen:
                raise ValueError("item_id 必须唯一且非空")
            if root_kind not in {"file", "directory"}:
                raise ValueError("root_kind 无效")
            if not isinstance(permissions, list) or not permissions:
                raise ValueError("permissions 不能为空")
            normalized = frozenset(str(permission) for permission in permissions)
            if not normalized <= _PERMISSIONS:
                raise ValueError("permissions 包含不支持的权限")
            path = resolve_locator(value.get("locator"))
            if root_kind == "file" and path.exists() and not path.is_file():
                raise ValueError(f"{item_id} 不是文件")
            if root_kind == "directory" and path.exists() and not path.is_dir():
                raise ValueError(f"{item_id} 不是目录")
            write_requested = bool(normalized & {"create", "edit"})
            if (
                root_kind == "directory"
                and self._policy.is_instruction_metadata(path)
                and normalized != frozenset({"search_metadata"})
            ):
                raise FilesystemError(
                    "protected_path",
                    "配置目录只允许目录级 search_metadata；读取或修改正文必须申请准确文件。",
                )
            if write_requested and not self._policy.generic_write_allowed(
                path,
                allow_instruction_metadata=self._policy.is_instruction_metadata(path),
            ):
                raise FilesystemError("protected_path", "产品内部状态不能请求通用写入。")
            allow_protected = self._policy.explicit_read_allowed(path)
            parsed.append(
                ApprovalItem(
                    item_id=item_id,
                    path=path,
                    root_kind=cast(Any, root_kind),
                    permissions=cast(frozenset[FilesystemPermission], normalized),
                    allow_protected=allow_protected,
                )
            )
            seen.add(item_id)
        return tuple(parsed)

    def _parse_actions(self, raw: object) -> tuple[ApprovalAction, ...]:
        if not isinstance(raw, list) or len(raw) > 16:
            raise ValueError("actions 必须是最多 16 项的数组")
        seen: set[str] = set()
        parsed: list[ApprovalAction] = []
        for value in raw:
            if not isinstance(value, dict):
                raise ValueError("actions 项必须是对象")
            action_id = value.get("action_id")
            action = value.get("action")
            if not isinstance(action_id, str) or not action_id or action_id in seen:
                raise ValueError("action_id 必须唯一且非空")
            if action not in _ACTIONS:
                raise ValueError("action 无效")
            target = resolve_locator(value.get("locator"))
            if not self._policy.generic_write_allowed(
                target,
                allow_instruction_metadata=self._policy.is_instruction_metadata(target),
            ):
                raise FilesystemError("protected_path", "产品内部状态不能请求破坏性写入。")
            expected_identity = None
            if target.exists():
                info = target.lstat()
                expected_identity = f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
            elif action in {"move", "delete"}:
                raise ValueError(f"{action} 目标不存在")
            parsed.append(
                ApprovalAction(
                    action_id=action_id,
                    action=cast(DestructiveAction, action),
                    target=target,
                    recursive=bool(value.get("recursive", False)),
                    expected_identity=expected_identity,
                )
            )
            seen.add(action_id)
        return tuple(parsed)


def _approval_audit(request: PendingFilesystemApproval) -> dict[str, object]:
    risk = request.risk_assessment
    return {
        "decision_source": request.decision_source,
        "approval_mode": request.approval_mode,
        "approval_mode_version": request.approval_mode_version,
        "risk_code": risk.code if risk is not None else None,
        "risk_fingerprint": risk.fingerprint if risk is not None else None,
    }


class FilesystemOperationTool:
    execution_kind = "serial"
    timeout_seconds: ClassVar[float] = 60.0

    def __init__(self, name: str, service: FilesystemService) -> None:
        self.name = name
        self._service = service
        self.description = _DESCRIPTIONS[name]
        self.parameters_schema = _SCHEMAS[name]
        if name in {
            "filesystem_search",
            "filesystem_list_directory",
            "filesystem_stat",
            "filesystem_read_text",
            "filesystem_read_text_tail",
        }:
            self.execution_kind = "parallel_read"

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            ledger = _ledger(args)
            public = {
                key: value for key, value in args.items() if not key.startswith("__agent_friend_")
            }
            public = _to_service_args(self.name, public)
            operation = getattr(self._service, self.name.removeprefix("filesystem_"))
            if self.name in {"filesystem_copy", "filesystem_move", "filesystem_delete"}:
                return self._invoke_batch(ledger, operation, public)
            payload = operation(ledger, **public)
            return _json_result({"ok": True, **payload})
        except FilesystemError as exc:
            message = str(exc)
            recovery: dict[str, object] = {}
            if exc.code == "permission_denied":
                message = (
                    f"{message} filesystem_scope_id 必须来自当前用户轮 request_filesystem_access "
                    "成功结果的 scopes[].filesystem_scope_id，或宿主提供的当前 Skill 文件 scope；"
                    "不能填 skill_id、request_id、旧用户轮或其它会话的 scope。不要使用同一 scope "
                    "重试，也不要只修改 max_lines、路径等其它参数；请改用当前用户轮已有的有效 "
                    "scope，若没有则调用一次 request_filesystem_access。只有真实授权成功结果才能"
                    "称为授权已刷新。"
                )
                recovery = _permission_recovery_fields()
            return _json_result(
                {
                    "ok": False,
                    "error": exc.code,
                    "message": message,
                    **exc.details,
                    **recovery,
                },
                error=True,
            )
        except (TypeError, ValueError) as exc:
            return _json_result(
                {"ok": False, "error": "invalid_arguments", "message": str(exc)},
                error=True,
            )

    def _invoke_batch(
        self,
        ledger: FilesystemAuthorizationLedger,
        operation: Any,
        public: dict[str, Any],
    ) -> ToolResult:
        raw_items = public.get("items")
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
            raise FilesystemError("invalid_arguments", "items 必须包含 1-50 项。")
        results: list[dict[str, object]] = []
        success_count = 0
        for index, raw in enumerate(raw_items):
            item_id = str(raw.get("item_id", index)) if isinstance(raw, dict) else str(index)
            if not isinstance(raw, dict):
                results.append(
                    {
                        "item_id": item_id,
                        "ok": False,
                        "error": "invalid_arguments",
                        "message": "批量项必须是对象。",
                    }
                )
                continue
            item_args = {key: value for key, value in raw.items() if key != "item_id"}
            try:
                payload = operation(ledger, **item_args)
            except FilesystemError as exc:
                results.append(
                    {
                        "item_id": item_id,
                        "ok": False,
                        "error": exc.code,
                        "message": str(exc),
                        **exc.details,
                    }
                )
            except (TypeError, ValueError) as exc:
                results.append(
                    {
                        "item_id": item_id,
                        "ok": False,
                        "error": "invalid_arguments",
                        "message": str(exc),
                    }
                )
            else:
                success_count += 1
                results.append({"item_id": item_id, "ok": True, **payload})
        status = (
            "success" if success_count == len(results) else "partial" if success_count else "failed"
        )
        summary: dict[str, object] = {}
        if (
            status == "failed"
            and results
            and all(item.get("error") == "permission_denied" for item in results)
        ):
            permission_reasons = {
                str(item["permission_reason"])
                for item in results
                if isinstance(item.get("permission_reason"), str)
            }
            if len(permission_reasons) == 1:
                summary = {
                    "error": "permission_denied",
                    "permission_reason": permission_reasons.pop(),
                    **_permission_recovery_fields(),
                }
        return _json_result(
            {"ok": status == "success", "status": status, "items": results, **summary},
            error=status != "success",
        )


def _to_service_args(name: str, public: dict[str, Any]) -> dict[str, Any]:
    """Translate semantic model-facing capability names to internal service names."""

    translated = dict(public)
    if name in {"filesystem_copy", "filesystem_move", "filesystem_delete"}:
        raw_items = translated.get("items")
        if isinstance(raw_items, list):
            items: list[object] = []
            for raw in raw_items:
                if not isinstance(raw, dict):
                    items.append(raw)
                    continue
                item = dict(raw)
                if "filesystem_scope_id" in item:
                    item["scope_id"] = item.pop("filesystem_scope_id")
                if "source_filesystem_scope_id" in item:
                    item["source_scope_id"] = item.pop("source_filesystem_scope_id")
                if "destination_filesystem_scope_id" in item:
                    item["destination_scope_id"] = item.pop("destination_filesystem_scope_id")
                items.append(item)
            translated["items"] = items
    elif "filesystem_scope_id" in translated:
        translated["scope_id"] = translated.pop("filesystem_scope_id")
    return translated


_FILESYSTEM_SCOPE_PROPERTY = {
    "type": "string",
    "minLength": 1,
    "description": (
        "当前用户轮 request_filesystem_access 成功结果的 "
        "scopes[].filesystem_scope_id，或宿主提供的当前 Skill 文件 scope。"
        "只能用于 filesystem_* 工具；不能填 skill_id、request_id、action_id、旧用户轮或其它会话 "
        "scope。失效后不能原样重试或通过修改其它参数恢复，必须使用当前用户轮的新 scope。"
    ),
}
_SCOPE_PATH_PROPERTIES = {
    "filesystem_scope_id": _FILESYSTEM_SCOPE_PROPERTY,
    "relative_path": {
        "type": "string",
        "default": ".",
        "description": "相对于授权根的路径；默认授权根本身，不得逃逸该根。",
    },
}
_SCHEMAS: dict[str, dict[str, Any]] = {
    "filesystem_search": {
        "type": "object",
        "properties": {
            "filesystem_scope_id": _FILESYSTEM_SCOPE_PROPERTY,
            "mode": {
                "type": "string",
                "enum": ["name", "content"],
                "description": "按名称/相对路径检索用 name；按正文检索用 content。",
            },
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "description": "要查找的文本；content 模式最多 4096 字符。",
            },
            "glob": {"type": "string", "description": "可选的相对路径 glob 过滤条件。"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "本页最多返回的匹配数。",
            },
            "continuation": {
                "type": "string",
                "description": "上一页结果返回的 continuation，只能与同一 filesystem_scope_id 原样复用。",
            },
        },
        "required": ["filesystem_scope_id", "mode", "query"],
        "additionalProperties": False,
    },
    "filesystem_list_directory": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "本页最多返回的直接子项数。",
            },
            "continuation": {
                "type": "string",
                "description": "上一页结果返回的 continuation，只能与同一 filesystem_scope_id 原样复用。",
            },
        },
        "required": ["filesystem_scope_id"],
        "additionalProperties": False,
    },
    "filesystem_stat": {
        "type": "object",
        "properties": _SCOPE_PATH_PROPERTIES,
        "required": ["filesystem_scope_id"],
        "additionalProperties": False,
    },
    "filesystem_read_text": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "从 1 开始的首行行号；省略时从文件开头读取。",
            },
            "start_column": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "start_line 内从 0 开始的字符偏移；通常省略。只有上一页返回了非零 "
                    "next_start_column 时，才把它与 next_start_line 一起原样复用。"
                ),
            },
            "max_lines": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2000,
                "description": (
                    "本次最多返回的文本行数；结果还受单次 10000 字符上限约束。"
                    "truncated=true 时按 next_start_line/next_start_column 继续。"
                ),
            },
            "encoding": {
                "type": "string",
                "enum": ["auto", "utf-8", "utf-8-sig", "utf-16", "gb18030"],
                "description": "文本解码方式；通常使用 auto，仅在已有证据明确编码时指定。",
            },
        },
        "required": ["filesystem_scope_id"],
        "additionalProperties": False,
    },
    "filesystem_read_text_tail": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "max_lines": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2000,
                "description": ("保留文件末尾的最多行数；结果仍受单次 10000 字符上限约束。"),
            },
            "encoding": {
                "type": "string",
                "enum": ["auto", "utf-8", "utf-8-sig", "utf-16", "gb18030"],
                "description": "文本解码方式；通常使用 auto，仅在已有证据明确编码时指定。",
            },
        },
        "required": ["filesystem_scope_id"],
        "additionalProperties": False,
    },
    "filesystem_write_text": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "content": {
                "type": "string",
                "description": "完整的新文件内容；创建缺失文件需要 create，不能用 edit 代替。",
            },
            "expected_sha256": {
                "type": "string",
                "description": "读取已有目标时返回的 SHA-256，用于并发安全覆盖。",
            },
            "overwrite_action_id": {
                "type": "string",
                "description": "已有目标覆盖时，同轮授权结果中针对该目标批准的 action_id。",
            },
        },
        "required": ["filesystem_scope_id", "relative_path", "content"],
        "additionalProperties": False,
    },
    "filesystem_apply_patch": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "patch": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "严格单文件 patch，必须逐字使用 *** Begin Patch、可选的 "
                    "*** Update File: <path>、一个或多个 @@ hunk、以及 *** End Patch；"
                    "hunk 每行必须以空格、+ 或 - 开头。"
                ),
            },
            "expected_sha256": {
                "type": "string",
                "minLength": 64,
                "maxLength": 64,
                "description": "filesystem_read_text 返回的当前文件 SHA-256。",
            },
        },
        "required": ["filesystem_scope_id", "relative_path", "patch", "expected_sha256"],
        "additionalProperties": False,
    },
    "filesystem_create_directory": {
        "type": "object",
        "properties": _SCOPE_PATH_PROPERTIES,
        "required": ["filesystem_scope_id", "relative_path"],
        "additionalProperties": False,
    },
    "filesystem_copy": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "description": "本次要复制的 1-50 个独立项目。",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "本次批量调用内由 Agent 自定的唯一项目标签。",
                        },
                        "source_filesystem_scope_id": _FILESYSTEM_SCOPE_PROPERTY,
                        "source_path": {
                            "type": "string",
                            "description": "相对于源授权根的文件或目录路径。",
                        },
                        "destination_filesystem_scope_id": _FILESYSTEM_SCOPE_PROPERTY,
                        "destination_path": {
                            "type": "string",
                            "description": "相对于目的授权根的新路径。",
                        },
                        "overwrite_action_id": {
                            "type": "string",
                            "description": (
                                "仅目的已存在时提供；必须来自同轮授权结果 actions[] 中"
                                "针对该准确目的目标批准的 action_id。"
                            ),
                        },
                    },
                    "required": [
                        "item_id",
                        "source_filesystem_scope_id",
                        "source_path",
                        "destination_filesystem_scope_id",
                        "destination_path",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
    "filesystem_move": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "description": "本次要移动的 1-50 个独立项目。",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "本次批量调用内由 Agent 自定的唯一项目标签。",
                        },
                        "source_filesystem_scope_id": _FILESYSTEM_SCOPE_PROPERTY,
                        "source_path": {
                            "type": "string",
                            "description": "相对于源授权根的准确路径。",
                        },
                        "destination_filesystem_scope_id": _FILESYSTEM_SCOPE_PROPERTY,
                        "destination_path": {
                            "type": "string",
                            "description": "相对于目的授权根的新路径。",
                        },
                        "action_id": {
                            "type": "string",
                            "description": (
                                "同轮授权结果 actions[] 中针对该准确源目标批准的 move action_id；"
                                "不是 filesystem_scope_id。"
                            ),
                        },
                    },
                    "required": [
                        "item_id",
                        "source_filesystem_scope_id",
                        "source_path",
                        "destination_filesystem_scope_id",
                        "destination_path",
                        "action_id",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
    "filesystem_delete": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "description": "本次要删除的 1-50 个准确目标。",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "本次批量调用内由 Agent 自定的唯一项目标签。",
                        },
                        **_SCOPE_PATH_PROPERTIES,
                        "action_id": {
                            "type": "string",
                            "description": (
                                "同轮授权结果 actions[] 中针对该准确目标批准的 delete action_id；"
                                "不是 filesystem_scope_id。"
                            ),
                        },
                        "recursive": {
                            "type": "boolean",
                            "default": False,
                            "description": "目录递归删除标志，必须与授权请求中批准的值一致。",
                        },
                    },
                    "required": [
                        "item_id",
                        "filesystem_scope_id",
                        "relative_path",
                        "action_id",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}
_DESCRIPTIONS = {
    "filesystem_search": "在已授权目录中按名称/相对路径或文本正文做有界流式搜索；包括大文本，但正文扫描总量、命中数和片段长度都有上限。content 模式返回命中行、列和短片段，search_metadata 不返回正文。",
    "filesystem_list_directory": "列举已授权目录的直接子项，稳定排序并支持 continuation。",
    "filesystem_stat": "查询已授权相对路径的类型、大小、mtime、身份摘要和保护分类。",
    "filesystem_read_text": "严格解码并有界分段读取已授权文本，包括大文件；单次正文最多 10000 字符，按返回的行/列游标继续；返回 SHA-256 供后续并发安全编辑。读取日志末尾时优先使用 filesystem_read_text_tail。",
    "filesystem_read_text_tail": "流式读取已授权文本的末尾若干行，适合大日志；内存与单次正文均有界，返回准确行号和 SHA-256。",
    "filesystem_write_text": "原子写入 UTF-8 文本。缺失目标需要 create；已有目标需要 edit、expected_sha256 和准确 overwrite action。edit 不包含 create。",
    "filesystem_apply_patch": "对单个已有文本应用严格 patch。必须使用字面量 *** Begin Patch / *** Update File: / @@ / *** End Patch，hunk 行以空格、+ 或 - 开头，并提供 expected_sha256。",
    "filesystem_create_directory": "在 create scope 内创建目录；edit 不能创建目录，也不会扩大授权范围。",
    "filesystem_copy": "字节透明复制文件或无链接目录。源 scope 需要 read_text，目的 scope 必须有 create（包括创建备份）；目的已存在时还需准确 overwrite action。",
    "filesystem_move": "移动准确目标。源 scope 需要 edit，目的 scope 需要 create，并需要绑定源目标的准确 move action。",
    "filesystem_delete": "删除准确目标；需要准确 delete action，递归属性必须与批准一致。",
}


def make_filesystem_tools(
    *,
    broker: FilesystemApprovalBroker | None = None,
    policy: ProtectedPathPolicy | None = None,
) -> list[Tool]:
    effective_policy = policy or ProtectedPathPolicy.default()
    service = FilesystemService(effective_policy)
    tools: list[Tool] = [RequestFilesystemAccessTool(broker, effective_policy)]
    tools.extend(
        cast(Tool, FilesystemOperationTool(name, service))
        for name in sorted(FILESYSTEM_TOOL_NAMES - {"request_filesystem_access"})
    )
    return tools
