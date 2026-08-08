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
        "filesystem_write_text",
        "filesystem_apply_patch",
        "filesystem_create_directory",
        "filesystem_copy",
        "filesystem_move",
        "filesystem_delete",
    }
)
_PERMISSIONS = frozenset({"search_metadata", "read_text", "create", "edit"})
_ACTIONS = frozenset({"overwrite", "move", "delete"})


def _json_result(payload: dict[str, object], *, error: bool = False) -> ToolResult:
    return ToolResult(
        text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        is_error=error,
        meta={"error_kind": payload.get("error")} if error else {},
    )


def _ledger(args: dict[str, Any]) -> FilesystemAuthorizationLedger:
    value = args.get(FILESYSTEM_LEDGER_ARG)
    if not isinstance(value, FilesystemAuthorizationLedger):
        raise FilesystemError("permission_denied", "当前用户轮没有文件授权上下文。")
    return value


class RequestFilesystemAccessTool:
    name: ClassVar[str] = "request_filesystem_access"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 310.0
    result_context_retention: ClassVar[str] = "pinned"
    description: ClassVar[str] = (
        "请求当前用户轮内的最小本地文件权限。普通会话默认没有任何用户文件权限；"
        "已知位置时请求准确文件或最小目录，不知道位置时先对合理的标准位置请求"
        "search_metadata。用户批准前不会执行文件操作。覆盖、移动、删除必须在 actions"
        "中列出准确目标。非桌面交互端会明确拒绝，不能假设获得权限。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "minLength": 1, "maxLength": 300},
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "minLength": 1, "maxLength": 80},
                        "locator": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["path", "standard"]},
                                "path": {"type": "string"},
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
                                },
                            },
                            "required": ["kind"],
                            "additionalProperties": False,
                        },
                        "root_kind": {"type": "string", "enum": ["file", "directory"]},
                        "permissions": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "string",
                                "enum": ["search_metadata", "read_text", "create", "edit"],
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
                "items": {
                    "type": "object",
                    "properties": {
                        "action_id": {"type": "string", "minLength": 1, "maxLength": 80},
                        "action": {"type": "string", "enum": ["overwrite", "move", "delete"]},
                        "locator": {"type": "object"},
                        "recursive": {"type": "boolean"},
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
        if args.get(FILESYSTEM_SURFACE_ARG) != "desktop_chat" or self._broker is None:
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
        decision = self._broker.request(request, timeout_seconds=300.0)
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
                        "scope_id": scope.scope_id,
                        "permissions": sorted(scope.permissions),
                    }
                )
            if permissions != item.permissions:
                denied_item_ids.append(item.item_id)
        granted_actions: list[dict[str, str]] = []
        for action in actions:
            if action.action_id in decision.approved_action_ids:
                ledger.grant_action(action)
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


class FilesystemOperationTool:
    result_context_retention: ClassVar[str] = "recoverable"
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
        }:
            self.execution_kind = "parallel_read"

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            ledger = _ledger(args)
            public = {
                key: value for key, value in args.items() if not key.startswith("__agent_friend_")
            }
            operation = getattr(self._service, self.name.removeprefix("filesystem_"))
            if self.name in {"filesystem_copy", "filesystem_move", "filesystem_delete"}:
                return self._invoke_batch(ledger, operation, public)
            payload = operation(ledger, **public)
            return _json_result({"ok": True, **payload})
        except FilesystemError as exc:
            return _json_result(
                {"ok": False, "error": exc.code, "message": str(exc), **exc.details},
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
        return _json_result(
            {"ok": status == "success", "status": status, "items": results},
            error=status != "success",
        )


_SCOPE_PATH_PROPERTIES = {
    "scope_id": {"type": "string", "minLength": 1},
    "relative_path": {"type": "string", "default": "."},
}
_SCHEMAS: dict[str, dict[str, Any]] = {
    "filesystem_search": {
        "type": "object",
        "properties": {
            "scope_id": {"type": "string"},
            "mode": {"type": "string", "enum": ["name", "content"]},
            "query": {"type": "string", "minLength": 1},
            "glob": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "continuation": {"type": "string"},
        },
        "required": ["scope_id", "mode", "query"],
        "additionalProperties": False,
    },
    "filesystem_list_directory": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "continuation": {"type": "string"},
        },
        "required": ["scope_id"],
        "additionalProperties": False,
    },
    "filesystem_stat": {
        "type": "object",
        "properties": _SCOPE_PATH_PROPERTIES,
        "required": ["scope_id"],
        "additionalProperties": False,
    },
    "filesystem_read_text": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "start_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
            "encoding": {
                "type": "string",
                "enum": ["auto", "utf-8", "utf-8-sig", "utf-16", "gb18030"],
            },
        },
        "required": ["scope_id"],
        "additionalProperties": False,
    },
    "filesystem_write_text": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "content": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "overwrite_action_id": {"type": "string"},
        },
        "required": ["scope_id", "relative_path", "content"],
        "additionalProperties": False,
    },
    "filesystem_apply_patch": {
        "type": "object",
        "properties": {
            **_SCOPE_PATH_PROPERTIES,
            "patch": {"type": "string", "minLength": 1},
            "expected_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
        },
        "required": ["scope_id", "relative_path", "patch", "expected_sha256"],
        "additionalProperties": False,
    },
    "filesystem_create_directory": {
        "type": "object",
        "properties": _SCOPE_PATH_PROPERTIES,
        "required": ["scope_id", "relative_path"],
        "additionalProperties": False,
    },
    "filesystem_copy": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "source_scope_id": {"type": "string"},
                        "source_path": {"type": "string"},
                        "destination_scope_id": {"type": "string"},
                        "destination_path": {"type": "string"},
                        "overwrite_action_id": {"type": "string"},
                    },
                    "required": [
                        "item_id",
                        "source_scope_id",
                        "source_path",
                        "destination_scope_id",
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
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "source_scope_id": {"type": "string"},
                        "source_path": {"type": "string"},
                        "destination_scope_id": {"type": "string"},
                        "destination_path": {"type": "string"},
                        "action_id": {"type": "string"},
                    },
                    "required": [
                        "item_id",
                        "source_scope_id",
                        "source_path",
                        "destination_scope_id",
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
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        **_SCOPE_PATH_PROPERTIES,
                        "action_id": {"type": "string"},
                        "recursive": {"type": "boolean"},
                    },
                    "required": ["item_id", "scope_id", "relative_path", "action_id"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}
_DESCRIPTIONS = {
    "filesystem_search": "在已授权目录中按名称/相对路径或文本正文做有界搜索；search_metadata 不返回正文。",
    "filesystem_list_directory": "列举已授权目录的直接子项，稳定排序并支持 continuation。",
    "filesystem_stat": "查询已授权相对路径的类型、大小、mtime、身份摘要和保护分类。",
    "filesystem_read_text": "严格解码并分段读取已授权文本；返回 SHA-256 供后续并发安全编辑。",
    "filesystem_write_text": "原子创建 UTF-8 文本；覆盖必须有 edit、expected_sha256 和准确 overwrite action。",
    "filesystem_apply_patch": "对单个已授权文本应用严格 Begin/Update/End patch；需要 expected_sha256。",
    "filesystem_create_directory": "在 create scope 内创建目录，不扩大授权范围。",
    "filesystem_copy": "在两个授权 scope 间字节透明复制文件或不含链接的目录。",
    "filesystem_move": "将准确源目标移动到已授权目的地；需要准确 move action。",
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
