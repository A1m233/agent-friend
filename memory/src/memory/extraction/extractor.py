"""``Extractor``：用 LLM 从一段对话 fragment 抽出 episodic 摘要 + semantic 操作。

复用注入的 :class:`llm_providers.LanguageModel`（design §3.6：v1 与主对话同 model，
构造可换独立 client）。本组件**只负责"调 LLM + 解析"**，不碰存储——落库由
:class:`Reconciler` 负责。

详见 docs/requirements/008-engine-memory/design.md §5.2。
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from llm_providers import ModelCallControl, ModelCallTraceContext, ModelRequest

from .context import ExtractionContext
from .result import (
    CoreUpdate,
    ExtractionAttempt,
    ExtractionAttemptPhase,
    ExtractionAttemptStatus,
    ExtractionOutput,
    SemanticChange,
    SemanticFactDraft,
    SemanticOp,
    SemanticRetirement,
)

if TYPE_CHECKING:
    from llm_providers import LanguageModel

    from ..contracts import ConversationFragment, SemanticUpdateTurn
    from ..store import SemanticRow

__all__ = ["Extractor"]

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"
_RECONCILIATION_PROMPT_PATH = Path(__file__).parent / "prompts" / "reconcile_semantic.md"
_CHARS_TO_TOKENS = 0.5
_SAFE_INPUT_RATIO = 0.80
_SEMANTIC_UPDATE_CONTEXT_RATIO = 0.25
_SEMANTIC_UPDATE_CONTEXT_MAX_TOKENS = 64_000
_FALLBACK_CONTEXT_WINDOW = 256 * 1024


@dataclass(frozen=True)
class _PackedSemanticUpdateContext:
    turns: tuple[SemanticUpdateTurn, ...]
    estimated_tokens: int
    omitted_turns: int
    fallback_reason: str | None = None

    @property
    def source_refs(self) -> tuple[str, ...]:
        return tuple(utterance.source_ref for turn in self.turns for utterance in turn.utterances)


class Extractor:
    """对话 fragment → :class:`ExtractionOutput` 的 LLM 抽取器。

    Args:
        llm_client: 抽取用的 LLM 客户端。
        prompt: 可选，覆盖默认抽取 system prompt（测试 / 调参用）。
    """

    def __init__(
        self,
        llm_client: LanguageModel,
        *,
        prompt: str | None = None,
        reconciliation_prompt: str | None = None,
        on_attempt: Callable[[ExtractionAttempt], None] | None = None,
        normalize_metadata: bool = True,
    ) -> None:
        self._llm = llm_client
        self._prompt = prompt if prompt is not None else _PROMPT_PATH.read_text(encoding="utf-8")
        self._reconciliation_prompt = (
            reconciliation_prompt
            if reconciliation_prompt is not None
            else _RECONCILIATION_PROMPT_PATH.read_text(encoding="utf-8")
        )
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
            raw = self._llm.generate(
                ModelRequest(
                    messages=messages,
                    control=ModelCallControl(
                        trace_context=ModelCallTraceContext(
                            operation="memory_extraction",
                            session_id=fragment.session_id,
                            call_kind="base_extraction",
                            call_index=1,
                        )
                    ),
                )
            ).text
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
        if output.validation_error is not None:
            return output
        return self._reconcile_semantic_changes(fragment, extraction_context, output)

    def _reconcile_semantic_changes(
        self,
        fragment: ConversationFragment,
        context: ExtractionContext,
        base_output: ExtractionOutput,
    ) -> ExtractionOutput:
        semantic_update_context = fragment.semantic_update_context
        if semantic_update_context is None or semantic_update_context.is_empty():
            return base_output

        mandatory_content = self._render_reconciliation_input(
            fragment,
            context,
            base_output,
            turns=(),
        )
        packed = _pack_semantic_update_turns(
            semantic_update_context.turns,
            context_window=_model_context_window(self._llm),
            mandatory_tokens=_estimate_tokens(
                self._reconciliation_prompt + "\n" + mandatory_content
            ),
        )
        if not packed.turns:
            logger.info(
                "semantic reconciliation skipped session=%s reason=%s omitted_turns=%d",
                fragment.session_id,
                packed.fallback_reason or "empty_after_capacity_pack",
                packed.omitted_turns,
            )
            return base_output

        user_content = self._render_reconciliation_input(
            fragment,
            context,
            base_output,
            turns=packed.turns,
        )
        messages = [
            {"role": "system", "content": self._reconciliation_prompt},
            {"role": "user", "content": user_content},
        ]
        started = perf_counter()
        try:
            raw = self._llm.generate(
                ModelRequest(
                    messages=messages,
                    control=ModelCallControl(
                        trace_context=ModelCallTraceContext(
                            operation="memory_reconciliation",
                            session_id=fragment.session_id,
                            call_kind="semantic_reconciliation",
                            call_index=2,
                        )
                    ),
                )
            ).text
        except Exception as exc:
            logger.warning("Semantic reconciliation LLM 调用失败，回退基础抽取", exc_info=True)
            self._report_attempt(
                fragment,
                context,
                phase="semantic_reconciliation",
                status="llm_error",
                started=started,
                error=exc,
                semantic_update_source_refs=packed.source_refs,
                semantic_update_selected_turns=len(packed.turns),
                semantic_update_capacity_omitted_turns=packed.omitted_turns,
                semantic_update_estimated_tokens=packed.estimated_tokens,
                semantic_reconciliation_fallback_reason="llm_error",
            )
            return base_output

        changes, status = _parse_reconciliation_with_status(raw)
        fallback_reason = _semantic_change_validation_error(changes, context)
        if status in ("empty_response", "invalid_json", "invalid_shape"):
            fallback_reason = status
        if fallback_reason is not None:
            self._report_attempt(
                fragment,
                context,
                phase="semantic_reconciliation",
                status=status,
                started=started,
                raw_response=raw,
                output=ExtractionOutput(semantic_changes=changes),
                semantic_update_source_refs=packed.source_refs,
                semantic_update_selected_turns=len(packed.turns),
                semantic_update_capacity_omitted_turns=packed.omitted_turns,
                semantic_update_estimated_tokens=packed.estimated_tokens,
                semantic_reconciliation_fallback_reason=fallback_reason,
            )
            return base_output

        reconciled = ExtractionOutput(
            core_updates=base_output.core_updates,
            semantic_changes=changes,
            episodic_entries=base_output.episodic_entries,
            output_contract_version=base_output.output_contract_version,
        )
        self._report_attempt(
            fragment,
            context,
            phase="semantic_reconciliation",
            status=status,
            started=started,
            raw_response=raw,
            output=ExtractionOutput(
                semantic_changes=changes,
                output_contract_version=base_output.output_contract_version,
            ),
            semantic_update_source_refs=packed.source_refs,
            semantic_update_selected_turns=len(packed.turns),
            semantic_update_capacity_omitted_turns=packed.omitted_turns,
            semantic_update_estimated_tokens=packed.estimated_tokens,
        )
        return reconciled

    def _report_attempt(
        self,
        fragment: ConversationFragment,
        context: ExtractionContext,
        *,
        phase: ExtractionAttemptPhase = "base",
        status: ExtractionAttemptStatus,
        started: float,
        raw_response: str | None = None,
        output: ExtractionOutput | None = None,
        error: Exception | None = None,
        semantic_update_source_refs: tuple[str, ...] = (),
        semantic_update_selected_turns: int = 0,
        semantic_update_capacity_omitted_turns: int = 0,
        semantic_update_estimated_tokens: int = 0,
        semantic_reconciliation_fallback_reason: str | None = None,
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
            semantic_count=(
                sum(len(change.new_facts) for change in output.semantic_changes)
                if output is not None
                else 0
            ),
            output_contract_version=(
                output.output_contract_version if output is not None else None
            ),
            raw_response=raw_response,
            error_type=type(error).__name__ if error is not None else None,
            error_message=str(error) if error is not None else None,
            phase=phase,
            semantic_update_source_refs=semantic_update_source_refs,
            semantic_update_selected_turns=semantic_update_selected_turns,
            semantic_update_capacity_omitted_turns=semantic_update_capacity_omitted_turns,
            semantic_update_estimated_tokens=semantic_update_estimated_tokens,
            semantic_reconciliation_fallback_reason=(semantic_reconciliation_fallback_reason),
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

    def _render_reconciliation_input(
        self,
        fragment: ConversationFragment,
        context: ExtractionContext,
        base_output: ExtractionOutput,
        *,
        turns: tuple[SemanticUpdateTurn, ...],
    ) -> str:
        lines: list[str] = ["## 当前有效普通 Semantic"]
        if context.semantic_facts:
            for ref, fact in context.semantic_facts:
                lines.append(f"[{ref}] {fact.statement}")
        else:
            lines.append("（暂无）")
        if context.semantic_omitted:
            lines.append(f"（另有 {context.semantic_omitted} 条未展示；不可引用或猜测这些条目。）")

        lines.extend(("", "## 只读邻近上下文"))
        if turns:
            for index, turn in enumerate(turns, start=1):
                lines.append(f"### 邻近轮次 {index}")
                for utterance in turn.utterances:
                    who = "用户" if utterance.speaker == "user" else "AI"
                    lines.append(f"{who}: {utterance.text}")
        else:
            lines.append("（容量计算占位；无正文）")

        lines.extend(("", "## 目标对话片段（唯一可写证据）"))
        for utterance in fragment.utterances:
            who = "用户" if utterance.speaker == "user" else "AI"
            lines.append(f"{who}: {utterance.text}")

        lines.extend(("", "## 基础抽取提出的 Semantic changes"))
        lines.append(
            json.dumps(
                {"semantic_changes": [asdict(change) for change in base_output.semantic_changes]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return "\n".join(lines)


def _model_context_window(llm: LanguageModel) -> int:
    try:
        context_window = llm.context_window
    except (AttributeError, RuntimeError, ValueError):
        return _FALLBACK_CONTEXT_WINDOW
    return context_window if context_window > 0 else _FALLBACK_CONTEXT_WINDOW


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) * _CHARS_TO_TOKENS))


def _turn_tokens(turn: SemanticUpdateTurn) -> int:
    return sum(
        _estimate_tokens(f"{'用户' if utterance.speaker == 'user' else 'AI'}: {utterance.text}")
        for utterance in turn.utterances
    )


def _pack_semantic_update_turns(
    turns: tuple[SemanticUpdateTurn, ...],
    *,
    context_window: int,
    mandatory_tokens: int,
) -> _PackedSemanticUpdateContext:
    if not turns:
        return _PackedSemanticUpdateContext((), 0, 0)

    safe_input_limit = math.floor(context_window * _SAFE_INPUT_RATIO)
    hard_budget = max(0, safe_input_limit - mandatory_tokens)
    soft_budget = min(
        _SEMANTIC_UPDATE_CONTEXT_MAX_TOKENS,
        math.floor(context_window * _SEMANTIC_UPDATE_CONTEXT_RATIO),
    )
    newest_cost = _turn_tokens(turns[-1])
    if newest_cost > hard_budget:
        return _PackedSemanticUpdateContext(
            (),
            0,
            len(turns),
            "latest_turn_exceeds_hard_budget",
        )

    budget = min(hard_budget, soft_budget)
    if newest_cost > budget:
        return _PackedSemanticUpdateContext(
            (turns[-1],),
            newest_cost,
            len(turns) - 1,
        )

    selected_reversed: list[SemanticUpdateTurn] = []
    used = 0
    for turn in reversed(turns):
        cost = _turn_tokens(turn)
        if used + cost > budget:
            break
        selected_reversed.append(turn)
        used += cost
    selected = tuple(reversed(selected_reversed))
    return _PackedSemanticUpdateContext(
        selected,
        used,
        len(turns) - len(selected),
    )


def _parse_reconciliation_with_status(
    raw: str,
) -> tuple[list[SemanticChange], ExtractionAttemptStatus]:
    text = _strip_code_fence(raw).strip()
    if not text:
        return [], "empty_response"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Semantic reconciliation 返回非法 JSON，回退基础抽取：%s", text[:200])
        return [], "invalid_json"
    if not isinstance(obj, dict) or set(obj) != {"semantic_changes"}:
        return [], "invalid_shape"
    raw_changes = obj.get("semantic_changes")
    if not isinstance(raw_changes, list):
        return [], "invalid_shape"
    changes: list[SemanticChange] = []
    for item in raw_changes:
        parsed = _parse_change(item)
        if parsed is None:
            return [], "invalid_shape"
        changes.append(parsed)
    return changes, "empty_output" if not changes else "success"


def _semantic_change_validation_error(
    changes: list[SemanticChange],
    context: ExtractionContext,
) -> str | None:
    visible_refs = context.semantic_ref_map
    retired_refs: set[str] = set()
    new_statements: set[str] = set()
    for change in changes:
        for retirement in change.retirements:
            if retirement.target_ref not in visible_refs:
                return "unknown_semantic_ref"
            if retirement.target_ref in retired_refs:
                return "duplicate_semantic_target"
            retired_refs.add(retirement.target_ref)
        for fact in change.new_facts:
            if fact.statement in new_statements:
                return "duplicate_semantic_statement"
            new_statements.add(fact.statement)
    return None


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

    has_changes = "semantic_changes" in obj
    has_ops = "semantic_ops" in obj
    if has_changes and has_ops:
        return ExtractionOutput(
            validation_error="ambiguous_semantic_contract",
            output_contract_version=2,
        ), "invalid_shape"

    output_contract_version = 2 if has_changes or not has_ops else 1
    strict_reasons = has_changes or "core_updates" in obj
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
    if has_changes and not isinstance(obj.get("episodic_entries"), list):
        invalid_operation = True

    changes: list[SemanticChange] = []
    ops: list[SemanticOp] = []
    if has_changes:
        raw_changes = obj.get("semantic_changes")
        if isinstance(raw_changes, list):
            for item in raw_changes:
                change = _parse_change(item)
                if change is None:
                    invalid_operation = True
                else:
                    changes.append(change)
        else:
            invalid_operation = True
    elif has_ops:
        raw_ops = obj.get("semantic_ops")
        if isinstance(raw_ops, list):
            for item in raw_ops:
                op = _parse_op(item, require_reason=strict_reasons)
                if op is None:
                    invalid_operation = True
                else:
                    ops.append(op)
        else:
            invalid_operation = True
    elif strict_reasons:
        invalid_operation = True
    if invalid_operation:
        return ExtractionOutput(
            validation_error="invalid_operation_shape_or_reason",
            output_contract_version=output_contract_version,
        ), "invalid_shape"
    output = ExtractionOutput(
        core_updates=core_updates,
        semantic_changes=changes,
        semantic_ops=ops,
        episodic_entries=entries,
        output_contract_version=output_contract_version,
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


def _parse_change(item: object) -> SemanticChange | None:
    if not isinstance(item, dict):
        return None
    raw_retirements = item.get("retirements")
    raw_new_facts = item.get("new_facts")
    if not isinstance(raw_retirements, list) or not isinstance(raw_new_facts, list):
        return None
    retirements: list[SemanticRetirement] = []
    for raw_retirement in raw_retirements:
        if not isinstance(raw_retirement, dict):
            return None
        target_ref = raw_retirement.get("target_ref")
        reason = raw_retirement.get("reason")
        if (
            not isinstance(target_ref, str)
            or not target_ref.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            return None
        retirements.append(SemanticRetirement(target_ref=target_ref.strip(), reason=reason.strip()))
    new_facts: list[SemanticFactDraft] = []
    for raw_fact in raw_new_facts:
        if not isinstance(raw_fact, dict):
            return None
        statement = raw_fact.get("statement")
        reason = raw_fact.get("reason")
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            return None
        importance = raw_fact.get("importance", 0.5)
        if not isinstance(importance, int | float):
            return None
        speaker_origin = raw_fact.get("speaker_origin", "user")
        if speaker_origin not in ("user", "agent"):
            return None
        new_facts.append(
            SemanticFactDraft(
                statement=statement.strip(),
                reason=reason.strip(),
                importance=max(0.0, min(1.0, float(importance))),
                speaker_origin=speaker_origin,
            )
        )
    if not retirements and not new_facts:
        return None
    return SemanticChange(retirements=retirements, new_facts=new_facts)


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
