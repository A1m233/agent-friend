"""当前会话 tool result 的有界只读恢复工具。"""

from __future__ import annotations

from typing import Any, ClassVar

from ....sessions.errors import SessionNotFoundError
from ....sessions.store import SessionStore
from ....sessions.tool_execution_evidence import (
    ToolExecutionEvidenceError,
    ToolExecutionEvidenceStore,
    render_recoverable_evidence,
)
from ...protocol import ToolResult

_CURRENT_SESSION_ID_ARG = "__agent_friend_current_session_id"
_DEFAULT_MAX_CHARS = 6_000
_MIN_MAX_CHARS = 500
_MAX_MAX_CHARS = 12_000


class ToolResultRecoveryTool:
    """只允许按已暴露引用读取当前会话的 active tool result。"""

    name: ClassVar[str] = "recover_tool_result"
    execution_kind: ClassVar[str] = "parallel_read"
    description: ClassVar[str] = (
        "仅当上下文原样给出了 result_ref 或中断命令结果原样给出了 "
        "evidence_ref、且回答确实需要其中的精确细节时使用。"
        "它只读当前会话，按 query 或 offset 分页返回有限片段；不要为了概览恢复整份结果，"
        "也不要猜测或传入其他会话标识。优先给 query 缩小范围；需要下一页时使用返回的 "
        "next_offset。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "result_ref": {
                "type": "string",
                "description": "清理占位符中原样给出的 result_ref。",
            },
            "evidence_ref": {
                "type": "string",
                "description": "中断命令结果中原样给出的 evidence_ref。",
            },
            "query": {
                "type": "string",
                "description": "可选；在原结果中查找的关键词或短语。",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "无 query 时是字符分页起点；有 query 时是搜索起点。",
            },
            "max_chars": {
                "type": "integer",
                "minimum": _MIN_MAX_CHARS,
                "maximum": _MAX_MAX_CHARS,
                "default": _DEFAULT_MAX_CHARS,
                "description": "本次最多返回的原文字符数。",
            },
        },
        "oneOf": [
            {"required": ["result_ref"], "not": {"required": ["evidence_ref"]}},
            {"required": ["evidence_ref"], "not": {"required": ["result_ref"]}},
        ],
        "additionalProperties": False,
    }

    def __init__(
        self,
        session_store: SessionStore,
        tool_execution_evidence_store: ToolExecutionEvidenceStore | None = None,
    ) -> None:
        self._session_store = session_store
        self._tool_execution_evidence_store = tool_execution_evidence_store

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        result_ref = args.get("result_ref")
        evidence_ref = args.get("evidence_ref")
        session_id = args.get(_CURRENT_SESSION_ID_ARG)
        has_result_ref = isinstance(result_ref, str) and bool(result_ref.strip())
        has_evidence_ref = isinstance(evidence_ref, str) and bool(evidence_ref.strip())
        if has_result_ref == has_evidence_ref:
            return ToolResult(
                "result_ref 和 evidence_ref 必须且只能提供一个非空字符串。",
                is_error=True,
            )
        if not isinstance(session_id, str) or not session_id:
            return ToolResult("当前会话上下文不可用，无法恢复工具结果。", is_error=True)

        offset = args.get("offset", 0)
        max_chars = args.get("max_chars", _DEFAULT_MAX_CHARS)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return ToolResult("offset 必须是大于等于 0 的整数。", is_error=True)
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or not _MIN_MAX_CHARS <= max_chars <= _MAX_MAX_CHARS
        ):
            return ToolResult(
                f"max_chars 必须是 {_MIN_MAX_CHARS} 到 {_MAX_MAX_CHARS} 的整数。",
                is_error=True,
            )

        try:
            session = self._session_store.load(session_id)
        except SessionNotFoundError:
            return ToolResult("当前会话不存在，无法恢复工具结果。", is_error=True)

        if has_result_ref:
            assert isinstance(result_ref, str)
            event = next(
                (
                    item
                    for item in session.active_events
                    if item.type == "tool_call_result" and item.uuid == result_ref
                ),
                None,
            )
        else:
            assert isinstance(evidence_ref, str)
            event = next(
                (
                    item
                    for item in session.active_events
                    if item.type == "tool_call_result"
                    and item.payload.get("evidence_ref") == evidence_ref
                ),
                None,
            )
        if event is None:
            return ToolResult(
                "当前会话的有效历史中没有这个引用。",
                is_error=True,
            )

        reference_label: str
        recovered_meta: dict[str, object]
        if has_result_ref:
            assert isinstance(result_ref, str)
            content = str(event.payload.get("content", ""))
            reference_label = f"result_ref={result_ref}"
            recovered_meta = {
                "context_retention": "recoverable",
                "recovered_result_ref": result_ref,
            }
        else:
            assert isinstance(evidence_ref, str)
            evidence_store = self._tool_execution_evidence_store
            if evidence_store is None:
                return ToolResult("当前运行实例不支持恢复命令证据。", is_error=True)
            try:
                evidence = evidence_store.load(session_id, evidence_ref)
            except ToolExecutionEvidenceError:
                return ToolResult("命令证据已损坏或无法读取。", is_error=True)
            if evidence is None:
                return ToolResult("当前会话中没有这个命令证据。", is_error=True)
            event_run_id = event.meta.get("run_id")
            event_tool_call_ids = {
                value
                for value in (
                    event.payload.get("event_tool_call_id"),
                    event.payload.get("tool_call_id"),
                )
                if isinstance(value, str) and value
            }
            if (
                evidence.run_id != (event_run_id if isinstance(event_run_id, str) else "")
                or evidence.tool_call_id not in event_tool_call_ids
                or evidence.tool_name != event.payload.get("tool_name")
            ):
                return ToolResult("命令证据与当前会话结果不匹配。", is_error=True)
            content = render_recoverable_evidence(evidence)
            reference_label = f"evidence_ref={evidence_ref}"
            recovered_meta = {
                "context_retention": "recoverable",
                "recovered_evidence_ref": evidence_ref,
            }
        query = args.get("query")
        if query is not None and (not isinstance(query, str) or not query.strip()):
            return ToolResult("query 若提供，必须是非空字符串。", is_error=True)

        start = min(offset, len(content))
        matched_at: int | None = None
        if isinstance(query, str):
            matched_at = content.lower().find(query.lower(), start)
            if matched_at < 0:
                return ToolResult(
                    f"{reference_label} 中从 offset={offset} 起未找到 query={query!r}。",
                    meta=recovered_meta,
                )
            start = max(0, matched_at - min(500, max_chars // 5))

        end = min(len(content), start + max_chars)
        next_offset = end if end < len(content) else None
        tool_name = str(event.payload.get("tool_name", "unknown"))
        header = (
            f"tool={tool_name}; {reference_label}; original_chars={len(content)}; "
            f"returned_range={start}:{end}; next_offset={next_offset}"
        )
        if matched_at is not None:
            header += f"; query_match_at={matched_at}"
        return ToolResult(
            f"{header}\n\n{content[start:end]}",
            meta=recovered_meta,
        )
