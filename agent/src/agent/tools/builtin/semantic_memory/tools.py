from __future__ import annotations

import json
from typing import Any, ClassVar

from memory.maintenance.models import MaintenanceError

from ....memory_maintenance.dispatch import MaintenanceDispatch
from ...protocol import Tool, ToolResult
from .definitions import DEFINITIONS


def _recovery(code: str) -> str:
    if code.startswith("stale_") or code == "batch_changed":
        return "目标版本已改变，本批未写入。用 audit.refresh 重读受影响条目，重新判断和 prepare；不要原样重试。"
    if code in {"ordinary_fact_not_found", "fact_outside_audit", "merge_target_outside_audit"}:
        return "目标不是本次可访问的普通记忆。使用 query/page 返回的 semantic_id；不要混用检查、批次或经历 ID。"
    if code == "audit_not_found":
        return "未找到所属用户的检查清单。用 audit.list 找回 audit_id；不要传 semantic_id 或 batch_id。"
    if code == "batch_not_found":
        return (
            "未找到所属用户的修改批次。用 changes.list 找回 batch_id；不要传 audit_id 或记忆 ID。"
        )
    if code in {"review_required", "decision_is_not_a_change", "overlapping_batch_changes"}:
        return "先核对检查清单的判断。prepare 只选择拟修改项，同一目标不能在一个批次被重复处置。"
    if code in {"invalid_evidence_ref", "evidence_required", "source_not_linked_to_fact"}:
        return "来源须来自该条 detail.source_refs 或宿主 current_user_source_ref。先读取原话；缺证时记录 unverified，不编造引用。"
    if code in {"source_reader_unavailable", "memory_unavailable"}:
        return "当前实例的记忆或来源服务不可用。报告该能力缺口，不把结果当作库存为空或检查完成。"
    if code.startswith("approval_") or code in {
        "user_initiated_maintenance_required",
        "batch_session_mismatch",
        "host_invocation_required",
        "execution_identity_missing",
        "authorization_session_mismatch",
        "host_authorization_required",
    }:
        return "当前请求未获执行条件或授权。不得自报批准、改用别的工具绕过；恢复会话与宿主授权后再处理该批次。"
    return "本次操作未完成。核对工具 schema、目标状态和参数；只在输入或前置条件改变后重试。"


class _MemoryTool:
    name: ClassVar[str]
    description: ClassVar[str]
    parameters_schema: ClassVar[dict[str, Any]]
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 330.0

    def __init__(self, dispatch: MaintenanceDispatch | None) -> None:
        self._dispatch = dispatch

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            if self._dispatch is None:
                raise MaintenanceError("memory_unavailable")
            value = self._dispatch.invoke(self.name, args)
            failed = bool(value.get("error"))
            if failed:
                value["message"] = _recovery(str(value["error"]))
            return ToolResult(
                text=json.dumps(value, ensure_ascii=False),
                is_error=failed,
                meta={"error_kind": value["error"]} if failed else {},
            )
        except MaintenanceError as exc:
            payload = {"status": "error", "error": exc.code, "message": _recovery(exc.code)}
            if exc.detail and exc.code in {"stale_audit_item", "stale_batch_target"}:
                payload["semantic_id"] = exc.detail
            return ToolResult(
                text=json.dumps(payload, ensure_ascii=False),
                is_error=True,
                meta={"error_kind": exc.code},
            )


class SemanticMemoryQueryTool(_MemoryTool):
    name = "semantic_memory_query"
    description = DEFINITIONS[name]["description"]
    parameters_schema = DEFINITIONS[name]["parameters"]


class SemanticMemoryDetailTool(_MemoryTool):
    name = "semantic_memory_detail"
    description = DEFINITIONS[name]["description"]
    parameters_schema = DEFINITIONS[name]["parameters"]


class SemanticMemorySourcesTool(_MemoryTool):
    name = "semantic_memory_sources"
    description = DEFINITIONS[name]["description"]
    parameters_schema = DEFINITIONS[name]["parameters"]


class SemanticMemoryAuditTool(_MemoryTool):
    name = "semantic_memory_audit"
    description = DEFINITIONS[name]["description"]
    parameters_schema = DEFINITIONS[name]["parameters"]


class SemanticMemoryChangesTool(_MemoryTool):
    name = "semantic_memory_changes"
    description = DEFINITIONS[name]["description"]
    parameters_schema = DEFINITIONS[name]["parameters"]


def make_semantic_memory_tools(dispatch: MaintenanceDispatch | None) -> list[Tool]:
    return [
        SemanticMemoryQueryTool(dispatch),
        SemanticMemoryDetailTool(dispatch),
        SemanticMemorySourcesTool(dispatch),
        SemanticMemoryAuditTool(dispatch),
        SemanticMemoryChangesTool(dispatch),
    ]
