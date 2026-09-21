"""``SummarizingContextManager``：带安全预算与 checkpoint 的摘要压缩主策略。

把长历史蒸馏成结构化 summary，腾出窗口同时尽量不丢关键信息。核心行为：

- **平时折叠**：用 ``runtime.prior_summary``（由 ``Conversation`` 从
  :meth:`Session.latest_compaction` 派生）把已覆盖的旧消息替换为一条 summary
  system 消息，省 token；估算仍在阈值内则直接返回。
- **超阈值触发摘要**：按 token 预算选择 recent tail 和可发送的连续摘要来源，不拆
  user/tool turn；首次历史已经过大时明确记录未进入摘要的旧 turn 数。
- **摘要后二次验算**：只有 summary + recent tail 仍满足主请求预算，才生成
  :class:`CompactionRecord` 候选。
- **circuit breaker**：同一 run 内限制连续失败；新 run / 模型可重新尝试。
  兜底仅允许不删除历史的结果投影；必须裁剪 turn 时暂停并保留历史。

⚠️ 摘要是**真实 LLM 调用**，运行时触发受 ``llm-api-confirm`` 约束。

详见 docs/requirements/063-context-compaction-reliability/design.md。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Generator
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from llm_providers import (
    LLMContextWindowError,
    LLMEmptyResponseError,
    LLMError,
    LLMUnsupportedParameterError,
    ModelCallControl,
    ModelCallTraceContext,
    ModelRequest,
    OperationRequestLineageTracker,
)

from ..attachments import AttachmentError, AttachmentRef
from ..messages import Message
from .budget import estimate_request_tokens, estimate_tokens
from .checkpoints import checkpoint_prefix_digest, summary_message
from .errors import (
    ContextBaseOverBudgetError,
    ContextCompactionUnavailableError,
    ContextInputTooLargeError,
)
from .fifo import FifoContextManager
from .protocol import (
    BuildResult,
    CompactionRecord,
    ContextCompactionProgress,
    ContextManager,
    PriorSummary,
    RuntimeContext,
    ToolContextEditRecord,
    assemble_messages,
)
from .summary_generation import generate_summary
from .summary_prompt import (
    SUMMARY_SYSTEM_PROMPT,
    build_summary_messages,
    render_transcript,
    strip_analysis,
)
from .tool_projection import ToolProjectionStats, project_tool_results
from .turns import preserve_latest_skill_catalog, recent_turn_start_candidates

MAX_CONSECUTIVE_FAILURES = 3
"""同一 run 内摘要连续失败多少次后跳闸。"""

SUMMARY_RECENT_TAIL_TURNS = 2
"""旧构造参数的兼容默认值；063 起 recent tail 完全由 token 预算决定。"""

POST_COMPACTION_TARGET_RATIO = 0.7
"""压缩后目标占用相对触发阈值的比例，给后续 turn 与 usage 校准留出余量。"""

SUMMARY_PROMPT_VERSION = 2
MAX_SUMMARY_CONTEXT_RETRIES = 2
SUMMARY_NETWORK_RETRIES = 1
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AssembleParts:
    """``build_messages`` 里"拼什么内容"的固定成分（区别于"裁/折"的 history）。"""

    system_prompt: str
    new_user_input: str | None
    new_user_attachments: list[AttachmentRef] | None
    extra_context: list[Message] | None
    pre_user_context: list[Message] | None
    turn_context: list[Message] | None
    trailing_user: str | None
    trailing_system: str | None

    def assemble(self, history: list[Message]) -> list[Message]:
        return assemble_messages(
            history=history,
            system_prompt=self.system_prompt,
            new_user_input=self.new_user_input,
            new_user_attachments=self.new_user_attachments,
            extra_context=self.extra_context,
            pre_user_context=self.pre_user_context,
            turn_context=self.turn_context,
            trailing_user=self.trailing_user,
            trailing_system=self.trailing_system,
        )


class SummarizingContextManager:
    """摘要压缩主策略（009 M3）。

    Args:
        fallback: 摘要不可用（跳闸 / 无可压缩较旧部分）时的兜底策略，默认
            :class:`FifoContextManager`。
        max_failures: circuit breaker 阈值，默认 :data:`MAX_CONSECUTIVE_FAILURES`。
        recent_tail_turns: 旧调用方兼容参数；063 起不再参与选择。
    """

    def __init__(
        self,
        fallback: ContextManager | None = None,
        *,
        max_failures: int = MAX_CONSECUTIVE_FAILURES,
        recent_tail_turns: int = SUMMARY_RECENT_TAIL_TURNS,
    ) -> None:
        self._fallback: ContextManager = fallback or FifoContextManager()
        self._max_failures = max_failures
        self._consecutive_failures = 0
        self._tripped = False
        self._failure_scope: tuple[str | None, str | None, str] | None = None
        self._unsupported_scope: tuple[str | None, str, str] | None = None
        self._prompt_cache_lineage = OperationRequestLineageTracker(max_entries=8)

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        new_user_attachments: list[AttachmentRef] | None = None,
        extra_context: list[Message] | None = None,
        pre_user_context: list[Message] | None = None,
        turn_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult:
        stream = self.stream_build_messages(
            history=history,
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            new_user_attachments=new_user_attachments,
            extra_context=extra_context,
            pre_user_context=pre_user_context,
            turn_context=turn_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
            runtime=runtime,
        )
        while True:
            try:
                next(stream)
            except StopIteration as stopped:
                return cast(BuildResult, stopped.value)

    def stream_build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        new_user_attachments: list[AttachmentRef] | None = None,
        extra_context: list[Message] | None = None,
        pre_user_context: list[Message] | None = None,
        turn_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> Generator[ContextCompactionProgress, None, BuildResult]:
        parts = _AssembleParts(
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            new_user_attachments=new_user_attachments,
            extra_context=extra_context,
            pre_user_context=pre_user_context,
            turn_context=turn_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )

        # runtime=None → 退化 Naive（不做预算判断，不变量 6）
        if runtime is None:
            return BuildResult(messages=parts.assemble(history))
        runtime.check_cancelled()

        failure_scope = (runtime.session_id, runtime.run_id, runtime.model_id)
        if runtime.run_id is None or failure_scope != self._failure_scope:
            self._failure_scope = failure_scope
            self._consecutive_failures = 0
            self._tripped = False

        # 已跳闸：只允许无历史丢失的兜底
        if self._tripped:
            return self._fallback_result(history, parts, runtime, reason="circuit_tripped")

        threshold = runtime.budget.trigger_threshold
        prior = runtime.prior_summary

        if parts.new_user_attachments:
            attachment_only = Message(
                role="user",
                content="",
                attachments=list(parts.new_user_attachments),
            )
            if estimate_tokens([attachment_only]) > threshold:
                raise AttachmentError(
                    "attachment_context_too_large",
                    "附件内容超过当前模型的上下文容量，请减少附件后重试。",
                )

        # 先折叠已摘要的旧段，再对仍活跃的 tool result 做低水位投影；避免重复处理
        # 已经被 summary 覆盖的巨大结果。
        active_history = self._fold_with_prior(history, prior)
        projected_history, projection = project_tool_results(
            active_history,
            runtime,
            protect_last_history_turn=new_user_input is None,
        )
        folded_msgs = parts.assemble(projected_history)
        est = estimate_request_tokens(folded_msgs, runtime)
        if est <= threshold:
            return BuildResult(
                messages=folded_msgs,
                new_tool_context_edit=projection.new_edit,
                notes={
                    "context_strategy": "summarizing",
                    "folded": prior is not None,
                    "token_estimate": est,
                    "tool_results_truncated": projection.truncated,
                    "tool_results_omitted": 0,
                    "tool_results_cleared": projection.cleared,
                    "tool_result_cleared_tokens": projection.cleared_tokens,
                    "final_strategy": "summary" if prior is not None else "full",
                },
            )

        blocked_scope = (
            (runtime.session_id, runtime.run_id, runtime.model_id) if runtime.run_id else None
        )
        if blocked_scope is not None and blocked_scope == self._unsupported_scope:
            return self._fallback_result(
                history, parts, runtime, reason="unsupported_model_parameter"
            )

        # 超阈值：重新蒸馏较旧部分
        full_projected_history, full_projection = project_tool_results(
            history,
            runtime,
            protect_last_history_turn=new_user_input is None,
            allow_new_edit=False,
            additional_cleared_result_uuids=projection.effective_cleared_result_uuids,
        )
        activity_id = str(uuid4())
        logger.info(
            "context_compaction_started session=%s run=%s model=%s "
            "estimated_tokens=%s threshold=%s effective_window=%s prior_summary=%s",
            runtime.session_id,
            runtime.run_id,
            runtime.model_id,
            est,
            threshold,
            runtime.budget.effective_window,
            prior is not None,
        )
        yield ContextCompactionProgress(activity_id=activity_id, phase="started")
        try:
            result = self._compact(
                full_projected_history,
                parts,
                runtime,
                threshold,
                full_projection,
                projection.new_edit,
                activity_id,
            )
        except Exception:
            yield ContextCompactionProgress(activity_id=activity_id, phase="discarded")
            raise
        if result.new_compaction is None:
            yield ContextCompactionProgress(activity_id=activity_id, phase="discarded")
        return result

    # ----- 触发摘要 -----

    def _compact(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        threshold: int,
        projection: ToolProjectionStats,
        new_tool_context_edit: ToolContextEditRecord | None,
        activity_id: str,
    ) -> BuildResult:
        summary_output_budget = max(512, min(4096, int(runtime.budget.effective_window * 0.05)))
        cut = self._recent_tail_cut(history, parts, runtime, summary_output_budget)
        if cut <= 0:
            # 没有可压缩的较旧部分（全在保护 tail / 无 user 边界）→ 检查无损兜底，
            # 不计失败、不跳闸（这不是摘要失败，是无从压缩）。
            return self._fallback_result(history, parts, runtime, reason="nothing_to_compact")

        older = history[:cut]
        tail = history[cut:]
        # A summary cannot repair an over-budget mandatory tail. Check the same
        # retained catalog/dynamic projection before spending a model call.
        retained = preserve_latest_skill_catalog(history, tail)
        if estimate_request_tokens(parts.assemble(retained), runtime) > threshold:
            return self._fallback_result(
                history,
                parts,
                runtime,
                reason="retained_context_over_budget",
            )
        summarizable_older = [
            message for message in older if not message.meta.get("model_only", False)
        ]
        raw = ""
        summary_source: list[Message] = []
        attempts = 0
        input_kind = "bounded_full"
        for attempt in range(MAX_SUMMARY_CONTEXT_RETRIES + 1):
            runtime.check_cancelled()
            attempts = attempt + 1
            ratio = 0.75**attempt
            summary_source = self._bounded_summary_source(
                summarizable_older,
                runtime,
                ratio=ratio,
            )
            if not summary_source:
                return self._fallback_result(
                    history, parts, runtime, reason="summary_input_too_large"
                )
            summary_text = render_transcript(summary_source)
            lineage = self._prompt_cache_lineage.begin(
                session_id=runtime.session_id,
                operation="context_compaction",
                call_kind="summary_generation",
                prompt_contract_revision=str(SUMMARY_PROMPT_VERSION),
            )
            try:
                runtime.check_cancelled()
                response = generate_summary(
                    runtime.llm_client,
                    ModelRequest(
                        messages=build_summary_messages(summary_text),
                        settings={"max_tokens": summary_output_budget},
                        control=ModelCallControl(
                            cancel_check=runtime.cancel_check,
                            retry_limit=SUMMARY_NETWORK_RETRIES,
                            previous_request_witness=lineage.previous_witness,
                            trace_context=ModelCallTraceContext(
                                operation="context_compaction",
                                surface=runtime.surface,
                                run_id=runtime.run_id,
                                session_id=runtime.session_id,
                                call_kind="summary_generation",
                                call_index=attempts,
                                recovery_attempt=attempt,
                                comparison_scope=lineage.scope,
                                comparison_precision=lineage.precision,
                                comparison=lineage.comparison_fields(),
                            ),
                        ),
                    ),
                )
                runtime.check_cancelled()
                self._prompt_cache_lineage.complete(lineage, response)
                raw = response.text
                break
            except LLMContextWindowError:
                runtime.check_cancelled()
                if attempt >= MAX_SUMMARY_CONTEXT_RETRIES:
                    return self._on_failure(
                        history, parts, runtime, reason="summary_context_window_exceeded"
                    )
            except LLMUnsupportedParameterError as exc:
                runtime.check_cancelled()
                if runtime.run_id:
                    self._unsupported_scope = (runtime.session_id, runtime.run_id, runtime.model_id)
                logger.warning(
                    "context_compaction_rejected session=%s run=%s model=%s "
                    "error_type=%s source=%s reason=%s",
                    runtime.session_id,
                    runtime.run_id,
                    runtime.model_id,
                    type(exc).__name__,
                    exc.error_source,
                    exc.reason_code,
                )
                return self._fallback_result(
                    history, parts, runtime, reason="unsupported_model_parameter"
                )
            except LLMEmptyResponseError:
                runtime.check_cancelled()
                return self._on_failure(history, parts, runtime, reason="empty_summary")
            except LLMError as exc:
                runtime.check_cancelled()
                try:
                    return self._on_failure(history, parts, runtime, reason="llm_error")
                except ContextBaseOverBudgetError:
                    # Fixed configuration cannot be repaired by retrying a summary.
                    raise
                except (ContextInputTooLargeError, ContextCompactionUnavailableError):
                    raise ContextCompactionUnavailableError(
                        "会话压缩未完成，已保留原历史。", reason="llm_error"
                    ) from exc
            except Exception:
                runtime.check_cancelled()
                return self._on_failure(history, parts, runtime, reason="llm_error")

        summary = strip_analysis(raw).strip()
        if not summary:
            return self._on_failure(history, parts, runtime, reason="empty_summary")

        # 摘要成功
        self._consecutive_failures = 0
        summary_uuid = str(uuid4())
        summary_msg = summary_message(summary, summary_uuid, activity_id)
        retained_tail = preserve_latest_skill_catalog(history, tail)
        msgs = parts.assemble([summary_msg, *retained_tail])
        tokens_after = estimate_request_tokens(msgs, runtime)
        if tokens_after > threshold:
            return self._fallback_result(
                history, parts, runtime, reason="summary_result_over_budget"
            )
        record = CompactionRecord(
            summary=summary,
            checkpoint_version=3,
            scope="model",
            covered_through_uuid=older[-1].uuid,
            tokens_before=estimate_request_tokens(parts.assemble(history), runtime),
            tokens_after=tokens_after,
            model=runtime.model_id,
            summary_prompt_version=SUMMARY_PROMPT_VERSION,
            summary_source_from_uuid=summary_source[0].uuid,
            retained_from_uuid=tail[0].uuid if tail else "",
            omitted_before_count=sum(
                1
                for message in older
                if message.role == "user"
                and message.uuid not in {item.uuid for item in summary_source}
            ),
            effective_window=runtime.budget.effective_window,
            calibration_factor=runtime.calibration_factor,
            trigger_reason=runtime.trigger_reason,
            summary_attempts=attempts,
            tool_results_truncated=projection.truncated,
            activity_id=activity_id,
            summary_message_uuid=summary_uuid,
            model_prefix_sha256=checkpoint_prefix_digest(message.uuid for message in older),
        )
        return BuildResult(
            messages=msgs,
            new_compaction=record,
            new_tool_context_edit=new_tool_context_edit,
            notes={
                "context_strategy": "summarizing",
                "compacted": True,
                "summary_input": input_kind,
                "token_estimate": record.tokens_after,
                "summary_attempts": attempts,
                "summary_input_tokens": estimate_tokens(
                    [
                        Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
                        Message(role="user", content=render_transcript(summary_source)),
                    ]
                ),
                "summary_input_budget": runtime.budget.trigger_threshold,
                "tool_results_truncated": projection.truncated,
                "tool_results_omitted": 0,
                "tool_results_cleared": projection.cleared,
                "tool_result_cleared_tokens": projection.cleared_tokens,
                "final_strategy": "summary",
            },
        )

    def _on_failure(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        *,
        reason: str,
    ) -> BuildResult:
        """摘要失败：同 run 计数，兜底仍须满足无历史丢失。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._max_failures:
            self._tripped = True
        return self._fallback_result(history, parts, runtime, reason=reason)

    def _fallback_result(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        *,
        reason: str,
    ) -> BuildResult:
        """检查兜底候选；有损候选不得交给 Conversation 持久化。"""
        try:
            result = self._fallback.build_messages(
                history=history,
                system_prompt=parts.system_prompt,
                new_user_input=parts.new_user_input,
                new_user_attachments=parts.new_user_attachments,
                extra_context=parts.extra_context,
                pre_user_context=parts.pre_user_context,
                turn_context=parts.turn_context,
                trailing_user=parts.trailing_user,
                trailing_system=parts.trailing_system,
                runtime=runtime,
            )
        except ContextBaseOverBudgetError:
            raise
        except ContextInputTooLargeError as exc:
            if reason == "nothing_to_compact":
                raise
            raise ContextCompactionUnavailableError(
                "必要的会话压缩未完成，已保留原历史。", reason=reason
            ) from exc
        if result.dropped_count or int(result.notes.get("tool_results_omitted") or 0):
            logger.warning(
                "context_compaction_paused session=%s run=%s reason=%s dropped_candidate=%s",
                runtime.session_id,
                runtime.run_id,
                reason,
                result.dropped_count,
            )
            raise ContextCompactionUnavailableError(
                "会话压缩未完成，已保留原历史；暂停这轮任务以避免丢失任务上下文。",
                reason=reason,
            )
        result.notes.setdefault("context_strategy", "summarizing")
        result.notes["fell_back"] = True
        result.notes["fallback_reason"] = reason
        result.notes["circuit_tripped"] = self._tripped
        return result

    # ----- 折叠 / 切分 / 摘要输入 -----

    def _fold_with_prior(self, history: list[Message], prior: PriorSummary | None) -> list[Message]:
        """用 prior_summary 把已覆盖的旧消息替换为一条 summary system 消息。

        ``covered_through_uuid`` 不在当前 history 中时（异常）→ 不折叠、返回原样。
        """
        if prior is None:
            return list(history)
        if getattr(prior, "materialized", False):
            return list(history)
        idx = self._index_of_uuid(history, prior.covered_through_uuid)
        if idx is None:
            return list(history)
        message_uuid = getattr(prior, "summary_message_uuid", "") or str(uuid4())
        summary_msg = summary_message(
            prior.summary, message_uuid, getattr(prior, "checkpoint_id", "")
        )
        return [
            summary_msg,
            *preserve_latest_skill_catalog(history, history[idx + 1 :]),
        ]

    def _bounded_summary_source(
        self,
        older: list[Message],
        runtime: RuntimeContext,
        *,
        ratio: float,
    ) -> list[Message]:
        """在摘要调用预算内选择连续的最新完整旧 turn。"""
        budget = int(runtime.budget.trigger_threshold * ratio)
        candidates = recent_turn_start_candidates(older)
        if candidates and candidates[-1] == len(older):
            candidates = candidates[:-1]
        if not candidates and older:
            candidates = [0]
        first_user = next(
            (index for index, message in enumerate(older) if message.role == "user"), len(older)
        )
        for start in candidates:
            runtime.check_cancelled()
            # The current model view can already contain a prior summary. Preserve
            # it even when a bounded recent source omits intervening raw turns.
            pinned = [
                message
                for index, message in enumerate(older[:start])
                if message.meta.get("synthetic") == "context_summary"
                or (
                    index < first_user
                    and message.role == "system"
                    and message.meta.get("synthetic") != "skill_catalog_state"
                )
            ]
            source = [*pinned, *older[start:]]
            transcript = render_transcript(source)
            request = build_summary_messages(transcript)
            summary_messages = [
                Message(role=message["role"], content=message["content"]) for message in request
            ]
            summary_request_tokens = math.ceil(
                estimate_tokens(summary_messages) * runtime.calibration_factor
            )
            if summary_request_tokens <= budget:
                return source
        return []

    def _recent_tail_cut(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        summary_output_budget: int,
    ) -> int:
        """选择连续 recent turn 后缀，并让压缩结果回落到稳定目标水位。"""
        user_indices = recent_turn_start_candidates(history)
        if user_indices and user_indices[-1] == len(history):
            user_indices = user_indices[:-1]
        if len(user_indices) <= 1:
            return 0
        placeholder_chars = max(1, int(summary_output_budget / 0.75))
        placeholder = Message(role="system", content="S" * placeholder_chars)
        target = int(runtime.budget.trigger_threshold * POST_COMPACTION_TARGET_RATIO)
        for cut in user_indices[1:]:
            runtime.check_cancelled()
            candidate = parts.assemble([placeholder, *history[cut:]])
            if estimate_request_tokens(candidate, runtime) <= target:
                return cut
        return user_indices[-1]

    @staticmethod
    def _index_of_uuid(messages: list[Message], uuid: str) -> int | None:
        for i, m in enumerate(messages):
            if m.uuid == uuid:
                return i
        return None
