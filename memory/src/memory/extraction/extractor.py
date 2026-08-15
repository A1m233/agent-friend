"""``Extractor``：用 LLM 从一段对话 fragment 抽出 episodic 摘要 + semantic 操作。

复用注入的 :class:`llm_providers.LLMClient`（design §3.6：v1 与主对话同 client，
构造可换独立 client）。本组件**只负责"调 LLM + 解析"**，不碰存储——落库由
:class:`Reconciler` 负责。

详见 docs/requirements/008-engine-memory/design.md §5.2。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from .context import ExtractionContext
from .result import (
    CoreUpdate,
    ExtractionAttempt,
    ExtractionAttemptStatus,
    ExtractionOutput,
    SemanticOp,
)

if TYPE_CHECKING:
    from llm_providers import LLMClient

    from ..contracts import ConversationFragment
    from ..store import SemanticRow

__all__ = ["Extractor"]

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"


class Extractor:
    """对话 fragment → :class:`ExtractionOutput` 的 LLM 抽取器。

    Args:
        llm_client: 抽取用的 LLM 客户端。
        prompt: 可选，覆盖默认抽取 system prompt（测试 / 调参用）。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        prompt: str | None = None,
        on_attempt: Callable[[ExtractionAttempt], None] | None = None,
        normalize_metadata: bool = True,
    ) -> None:
        self._llm = llm_client
        self._prompt = prompt if prompt is not None else _PROMPT_PATH.read_text(encoding="utf-8")
        self._on_attempt = on_attempt
        # Kept as an inert compatibility argument for older composition roots.  Core
        # classification is now exclusively an extractor schema decision.
        self._normalize_metadata = normalize_metadata

    def extract(
        self,
        fragment: ConversationFragment,
        context: ExtractionContext | list[SemanticRow],
    ) -> ExtractionOutput:
        """抽取一个 fragment。

        失败（LLM 报错 / 返回非法 JSON）时**返回空 output、不抛异常**——抽取是
        尽力而为的旁路，不应让 worker 崩溃或污染对话。
        """
        extraction_context = (
            context
            if isinstance(context, ExtractionContext)
            else ExtractionContext.from_existing(context)
        )
        user_content = self._render_input(fragment, extraction_context)
        messages = [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": user_content},
        ]
        started = perf_counter()
        try:
            raw = self._llm.complete(messages)
        except Exception as exc:
            logger.warning("记忆抽取 LLM 调用失败，跳过本段", exc_info=True)
            self._report_attempt(
                fragment,
                extraction_context,
                status="llm_error",
                started=started,
                error=exc,
            )
            return ExtractionOutput()

        output, status = _parse_output_with_status(raw)
        self._report_attempt(
            fragment,
            extraction_context,
            status=status,
            started=started,
            raw_response=raw,
            output=output,
        )
        return output

    def _report_attempt(
        self,
        fragment: ConversationFragment,
        context: ExtractionContext,
        *,
        status: ExtractionAttemptStatus,
        started: float,
        raw_response: str | None = None,
        output: ExtractionOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        if self._on_attempt is None:
            return
        source_refs = [utterance.source_ref for utterance in fragment.utterances]
        attempt = ExtractionAttempt(
            session_id=fragment.session_id,
            first_source_ref=source_refs[0] if source_refs else None,
            last_source_ref=source_refs[-1] if source_refs else None,
            utterance_count=len(fragment.utterances),
            existing_fact_count=len(context.semantic_facts),
            status=status,
            duration_ms=(perf_counter() - started) * 1000,
            core_context_count=len(context.core_entries),
            semantic_total=context.semantic_total,
            semantic_omitted=context.semantic_omitted,
            core_update_count=len(output.core_updates) if output is not None else 0,
            episodic_count=len(output.episodic_entries) if output is not None else 0,
            semantic_count=len(output.semantic_ops) if output is not None else 0,
            raw_response=raw_response,
            error_type=type(error).__name__ if error is not None else None,
            error_message=str(error) if error is not None else None,
        )
        try:
            self._on_attempt(attempt)
        except Exception:
            logger.warning("记忆抽取 attempt 回调异常", exc_info=True)

    def _render_input(self, fragment: ConversationFragment, context: ExtractionContext) -> str:
        lines: list[str] = ["## 当前完整 Core Profile"]
        if context.core_entries:
            for ref, entry in context.core_entries:
                scope = "全局用户" if entry.scope_kind == "user" else f"persona={entry.persona_id}"
                lines.append(f"[{ref}] {entry.field_key} = {entry.value} ({scope})")
        else:
            lines.append("（暂无）")
        lines.extend(("", "## 当前有效普通 Semantic"))
        if context.semantic_facts:
            for ref, fact in context.semantic_facts:
                lines.append(f"[{ref}] {fact.statement}")
        else:
            lines.append("（暂无）")
        if context.semantic_omitted:
            lines.append(
                f"（另有 {context.semantic_omitted} 条因 50000 token 预算未展示；"
                "不可引用或猜测这些条目。）"
            )
        lines.append("")
        lines.append("## 对话片段")
        for u in fragment.utterances:
            who = "用户" if u.speaker == "user" else "AI"
            lines.append(f"{who}: {u.text}")
        return "\n".join(lines)


def _parse_output(raw: str) -> ExtractionOutput:
    """把 LLM 文本解析成 :class:`ExtractionOutput`，尽量鲁棒。

    兼容新旧 prompt 输出（pass-1）：

    - 新字段 ``episodic_entries: list[str]``（推荐）
    - 旧字段 ``episodic_summary: str | None``——单元素 list 处理（向下兼容）
    """
    return _parse_output_with_status(raw)[0]


def _parse_output_with_status(raw: str) -> tuple[ExtractionOutput, ExtractionAttemptStatus]:
    text = _strip_code_fence(raw).strip()
    if not text:
        return ExtractionOutput(), "empty_response"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("记忆抽取返回非法 JSON，跳过：%s", text[:200])
        return ExtractionOutput(), "invalid_json"
    if not isinstance(obj, dict):
        return ExtractionOutput(), "invalid_shape"

    strict_reasons = "core_updates" in obj
    core_updates: list[CoreUpdate] = []
    invalid_operation = False
    raw_core = obj.get("core_updates")
    if isinstance(raw_core, list):
        for item in raw_core:
            parsed = _parse_core_update(item, require_reason=strict_reasons)
            if parsed is None:
                invalid_operation = True
            else:
                core_updates.append(parsed)
    elif strict_reasons:
        invalid_operation = True

    entries = _parse_entries(obj)

    ops: list[SemanticOp] = []
    raw_ops = obj.get("semantic_ops")
    if isinstance(raw_ops, list):
        for item in raw_ops:
            op = _parse_op(item, require_reason=strict_reasons)
            if op is None:
                invalid_operation = True
            else:
                ops.append(op)
    elif strict_reasons:
        invalid_operation = True
    if invalid_operation:
        return ExtractionOutput(
            validation_error="invalid_operation_shape_or_reason"
        ), "invalid_shape"
    output = ExtractionOutput(
        core_updates=core_updates,
        semantic_ops=ops,
        episodic_entries=entries,
    )
    return output, "empty_output" if output.is_noop() else "success"


def _parse_entries(obj: dict[str, object]) -> list[str]:
    """从 LLM JSON 抽取 episodic 条目，兼容新旧字段。

    新字段优先：``episodic_entries: list[str]``。旧字段 fallback：
    ``episodic_summary: str | null``——非空字符串当单元素 list；其他都视为空。
    """
    entries_raw = obj.get("episodic_entries")
    if isinstance(entries_raw, list):
        cleaned = [e.strip() for e in entries_raw if isinstance(e, str) and e.strip()]
        if cleaned:
            return cleaned

    summary = obj.get("episodic_summary")
    if isinstance(summary, str) and summary.strip():
        return [summary.strip()]

    return []


def _parse_core_update(item: object, *, require_reason: bool) -> CoreUpdate | None:
    if not isinstance(item, dict):
        return None
    op = item.get("op")
    field = item.get("field")
    if op not in ("add", "replace", "remove"):
        return None
    if field not in (
        "user.preferred_name",
        "user.current_residences",
        "relationship.kind",
        "relationship.agent_nickname",
    ):
        return None
    value = item.get("value")
    if not isinstance(value, str) or not value.strip():
        value = None
    target_ref = item.get("target_ref")
    if not isinstance(target_ref, str) or not target_ref.strip():
        target_ref = None
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        if require_reason:
            return None
        reason = "legacy extractor output without classification rationale"
    return CoreUpdate(
        op=op,
        field=field,
        reason=reason.strip(),
        value=value,
        target_ref=target_ref,
    )


def _parse_op(item: object, *, require_reason: bool) -> SemanticOp | None:
    if not isinstance(item, dict):
        return None
    statement = item.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        return None
    op = item.get("op")
    if op not in ("add", "supersede"):
        return None
    importance = item.get("importance", 0.5)
    if not isinstance(importance, int | float):
        importance = 0.5
    importance = max(0.0, min(1.0, float(importance)))
    speaker_origin = item.get("speaker_origin", "user")
    if speaker_origin not in ("user", "agent"):
        speaker_origin = "user"
    target_ref = item.get("target_ref")
    if not isinstance(target_ref, str) or not target_ref.strip():
        target_ref = None
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        if require_reason:
            return None
        reason = "legacy extractor output without classification rationale"
    return SemanticOp(
        op=op,
        statement=statement.strip(),
        reason=reason.strip(),
        importance=importance,
        speaker_origin=speaker_origin,
        target_ref=target_ref,
    )


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏（如果模型不听话加了）。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
