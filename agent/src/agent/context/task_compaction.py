"""Bounded compaction of complete response/tool groups within a running task."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from hashlib import sha256
from uuid import uuid4

from llm_providers.errors import LLMIncompleteResponseError, LLMOutputBudgetExhaustedError

from llm_providers import (
    LLMContextWindowError,
    LLMEmptyResponseError,
    LLMError,
    LLMNetworkError,
    LLMTransientProviderError,
    LLMUnsupportedParameterError,
    ModelCallControl,
    ModelCallTraceContext,
    ModelRequest,
    OperationRequestLineageTracker,
    get_model_capabilities,
)

from ..messages import Message
from .budget import assess_request_budget, estimate_request_tokens, estimate_tokens
from .checkpoints import checkpoint_prefix_digest, summary_message
from .errors import ContextCompactionUnavailableError
from .protocol import (
    MAX_SUMMARY_REQUESTS,
    MAX_SUMMARY_SEGMENTS,
    BuildResult,
    CompactionControl,
    CompactionRecord,
    RuntimeContext,
    ToolContextEditRecord,
)
from .summary_budget import (
    summary_generation_tokens,
    summary_output_exhausted,
    summary_target_tokens,
)
from .summary_generation import generate_summary
from .summary_prompt import build_summary_messages, render_transcript, strip_analysis
from .task_anchor import (
    TASK_ANCHOR_VERSION,
    anchor_content_digest,
    message_user_source_digest,
    task_anchor_message,
)
from .tool_projection import ToolProjectionStats
from .turns import (
    preserve_latest_skill_catalog,
    recent_turn_start_candidates,
    safe_compaction_boundaries,
)

Assemble = Callable[[list[Message]], list[Message]]


def _failure(reason: str) -> ContextCompactionUnavailableError:
    return ContextCompactionUnavailableError("会话压缩未完成，已保留原历史。", reason=reason)


def _fingerprint(messages: list[Message]) -> str:
    # The diagnostic stores only the digest; never opaque payloads or source text.
    return sha256(
        json.dumps(
            [
                {
                    **message.to_openai(),
                    "reasoning": message.meta.get("provider_reasoning_state"),
                }
                for message in messages
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _anchor_for_prefix(prefix: list[Message], control: CompactionControl) -> Message | None:
    user = control.current_user
    if user is None:
        return None
    if any(
        message.uuid == user.uuid
        or (message.role == "user" and message.meta.get("source_user_event_uuid") == user.uuid)
        for message in prefix
    ):
        return task_anchor_message(user)
    return None


def _select_prefix(
    history: list[Message],
    assemble: Assemble,
    runtime: RuntimeContext,
    control: CompactionControl,
    target_tokens: int,
    has_new_user_input: bool,
) -> tuple[int, Message | None, bool]:
    try:
        boundaries = safe_compaction_boundaries(history)
    except ValueError as exc:
        raise _failure("tool_transaction_pending") from exc
    ordinary = [
        cut
        for cut in recent_turn_start_candidates(history)
        if 0 < cut < len(history) and cut in boundaries
    ]
    if has_new_user_input and history:
        ordinary.append(len(history))
    placeholder = Message(role="system", content="S" * max(1, math.ceil(target_tokens / 0.75)))
    threshold = runtime.budget.trigger_threshold
    target = int(threshold * 0.7)

    def choose(cuts: list[int], task_internal: bool) -> tuple[int, Message | None, bool] | None:
        possible: tuple[int, Message | None, bool] | None = None
        short_summary_possible: tuple[int, Message | None, bool] | None = None
        for cut in cuts:
            runtime.check_cancelled()
            anchor = _anchor_for_prefix(history[:cut], control)
            if task_internal and not has_new_user_input and control.current_user is not None:
                # Older rollouts did not bind rendered user UUIDs to business
                # UUIDs. Pin the known business goal without guessing that map.
                anchor = task_anchor_message(control.current_user)
            retained = preserve_latest_skill_catalog(history, history[cut:])
            candidate = assemble([placeholder, *([anchor] if anchor else []), *retained])
            assessment = assess_request_budget(candidate, runtime)
            if not assessment.unknown_components and assessment.tokens <= threshold:
                possible = (cut, anchor, task_internal)
                if assessment.tokens <= target:
                    return possible
            else:
                # The retained-text target is a reserve, not a minimum summary
                # size. A short valid summary may fit when the placeholder does
                # not; the actual complete candidate is checked again below.
                mandatory = assess_request_budget(
                    assemble([*([anchor] if anchor else []), *retained]), runtime
                )
                if not mandatory.unknown_components and mandatory.tokens < threshold:
                    short_summary_possible = (cut, anchor, task_internal)
        return possible or short_summary_possible

    choice = choose(ordinary, False)
    if choice is not None:
        return choice
    capable = (
        get_model_capabilities(runtime.llm_client).context_checkpoint_mode
        == "replace_complete_groups"
    )
    if not capable:
        raise _failure("checkpoint_unsupported")
    if control.current_user is None and not has_new_user_input:
        raise _failure("task_source_unavailable")
    choice = choose([cut for cut in boundaries if cut not in ordinary], True)
    if choice is not None:
        return choice
    anchor = task_anchor_message(control.current_user) if control.current_user else None
    minimum = assess_request_budget(assemble([anchor] if anchor else []), runtime)
    if not minimum.input_cost_known or minimum.is_upper_bound:
        raise _failure("input_cost_unknown")
    raise _failure("retained_context_over_budget")


def _summary_request(
    source: list[Message], previous: str, goal: Message | None, target_tokens: int
) -> list[dict[str, str]]:
    sections = []
    if goal is not None:
        sections.append("【资料分区：当前业务目标来源】\n" + render_transcript([goal]))
    if previous:
        sections.append("【资料分区：此前已归纳的连续前缀】\n" + previous)
    sections.append("【资料分区：接续的原始记录】\n" + render_transcript(source))
    return build_summary_messages("\n\n".join(sections), target_tokens=target_tokens)


def _request_tokens(messages: list[dict[str, str]], runtime: RuntimeContext) -> int:
    return math.ceil(
        estimate_tokens([Message(role="system", content=item["content"]) for item in messages])
        * runtime.calibration_factor
    )


def _summarize_prefix(
    prefix: list[Message],
    runtime: RuntimeContext,
    control: CompactionControl,
    target_tokens: int,
    prompt_version: int,
    lineage_tracker: OperationRequestLineageTracker,
) -> str:
    # Boundaries are computed before excluding model-only projection items. No
    # omitted task transaction may be silently claimed as summarized coverage.
    ends = safe_compaction_boundaries(prefix)
    cursor = 0
    previous = ""
    source_keys = tuple(
        sha256(
            json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        for message in prefix
    )
    goal_fingerprint = (
        f"{control.current_user.uuid}:{message_user_source_digest(control.current_user)}"
        if control.current_user
        else ""
    )
    completed_count = len(control.summarized_source)
    if (
        control.summary_text
        and completed_count in ends
        and source_keys[:completed_count] == control.summarized_source
        and control.summary_goal_fingerprint == goal_fingerprint
    ):
        # Provider capacity recovery can reuse an uncommitted, verified summary
        # prefix. It remains transient and consumes no fresh request allowance.
        cursor = completed_count
        previous = control.summary_text
    ratio = 1.0
    transient_retry_used = False
    retry_fingerprint: str | None = None
    while cursor < len(prefix):
        runtime.check_cancelled()
        if control.summary_requests >= MAX_SUMMARY_REQUESTS:
            raise _failure("summary_attempt_limit")
        if control.successful_segments >= MAX_SUMMARY_SEGMENTS:
            raise _failure("summary_segment_limit")
        chosen = cursor
        request_messages: list[dict[str, str]] = []
        budget = int(runtime.budget.trigger_threshold * ratio)
        for end in ends:
            if end <= cursor:
                continue
            source = [
                message for message in prefix[cursor:end] if not message.meta.get("model_only")
            ]
            candidate = _summary_request(source, previous, control.current_user, target_tokens)
            if _request_tokens(candidate, runtime) > budget:
                break
            chosen, request_messages = end, candidate
        if chosen == cursor:
            raise _failure("summary_input_too_large")
        generation_tokens = summary_generation_tokens(
            request_messages, runtime, expanded=control.summary_output_retry_used
        )
        if generation_tokens <= 0:
            raise _failure("summary_input_too_large")
        fingerprint = sha256(
            json.dumps(request_messages, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if fingerprint in control.attempted_inputs and fingerprint != retry_fingerprint:
            raise _failure("compaction_no_progress")
        retry_fingerprint = None
        control.attempted_inputs.add(fingerprint)
        control.summary_requests += 1
        lineage = lineage_tracker.begin(
            session_id=runtime.session_id,
            operation="context_compaction",
            call_kind="summary_generation",
            prompt_contract_revision=str(prompt_version),
        )
        try:
            response = generate_summary(
                runtime.llm_client,
                ModelRequest(
                    messages=list(request_messages),
                    settings={"max_tokens": generation_tokens},
                    control=ModelCallControl(
                        cancel_check=runtime.cancel_check,
                        retry_limit=0,
                        previous_request_witness=lineage.previous_witness,
                        trace_context=ModelCallTraceContext(
                            operation="context_compaction",
                            surface=runtime.surface,
                            run_id=runtime.run_id,
                            session_id=runtime.session_id,
                            call_kind="summary_generation",
                            call_index=control.summary_requests,
                            recovery_attempt=control.summary_requests - 1,
                            comparison_scope=lineage.scope,
                            comparison_precision=lineage.precision,
                            comparison=lineage.comparison_fields(),
                        ),
                    ),
                ),
            )
            runtime.check_cancelled()
            lineage_tracker.complete(lineage, response)
        except LLMContextWindowError:
            runtime.check_cancelled()
            ratio *= 0.75
            continue
        except (LLMNetworkError, LLMTransientProviderError) as exc:
            runtime.check_cancelled()
            if transient_retry_used:
                raise _failure("llm_error") from exc
            transient_retry_used = True
            retry_fingerprint = fingerprint
            continue
        except LLMUnsupportedParameterError as exc:
            runtime.check_cancelled()
            raise _failure("unsupported_model_parameter") from exc
        except (LLMIncompleteResponseError, LLMOutputBudgetExhaustedError) as exc:
            runtime.check_cancelled()
            exhausted = summary_output_exhausted(exc)
            expanded_tokens = summary_generation_tokens(request_messages, runtime, expanded=True)
            if (
                exhausted
                and not control.summary_output_retry_used
                and expanded_tokens > generation_tokens
                and control.summary_requests < MAX_SUMMARY_REQUESTS
            ):
                control.summary_output_retry_used = True
                retry_fingerprint = fingerprint
                continue
            raise _failure(
                "summary_output_budget_exhausted" if exhausted else "summary_incomplete"
            ) from exc
        except LLMEmptyResponseError as exc:
            runtime.check_cancelled()
            raise _failure("empty_summary") from exc
        except LLMError as exc:
            runtime.check_cancelled()
            raise _failure("llm_error") from exc
        except Exception as exc:
            # Cancellation remains owned by the supplied cancellation token and
            # must escape unchanged, including cancellation during stream reads.
            runtime.check_cancelled()
            raise _failure("llm_error") from exc
        previous = strip_analysis(response.text).strip()
        if not previous:
            raise _failure("empty_summary")
        control.successful_segments += 1
        cursor = chosen
        control.summarized_source = source_keys[:cursor]
        control.summary_text = previous
        control.summary_goal_fingerprint = goal_fingerprint
    return previous


def compact_task_history(
    history: list[Message],
    assemble: Assemble,
    runtime: RuntimeContext,
    *,
    has_new_user_input: bool,
    projection: ToolProjectionStats,
    new_tool_context_edit: ToolContextEditRecord | None,
    activity_id: str,
    prompt_version: int,
    lineage_tracker: OperationRequestLineageTracker,
) -> BuildResult:
    """Produce a candidate only; Conversation owns acceptance and durable commit."""
    control = runtime.compaction_control
    if control is None:
        raise ValueError("compaction_control_required")
    if not history:
        raise _failure("nothing_to_compact")
    target_tokens = summary_target_tokens(runtime)
    cut, anchor, task_internal = _select_prefix(
        history, assemble, runtime, control, target_tokens, has_new_user_input
    )
    prefix, tail = history[:cut], history[cut:]
    summary = _summarize_prefix(
        prefix, runtime, control, target_tokens, prompt_version, lineage_tracker
    )
    summary_uuid = str(uuid4())
    retained = preserve_latest_skill_catalog(history, tail)
    messages = assemble(
        [
            summary_message(summary, summary_uuid, activity_id),
            *([anchor] if anchor else []),
            *retained,
        ]
    )
    assessment = assess_request_budget(messages, runtime)
    if assessment.unknown_components:
        raise _failure("input_cost_unknown")
    if assessment.tokens > runtime.budget.trigger_threshold:
        raise _failure(
            "input_cost_unknown" if assessment.is_upper_bound else "summary_result_over_budget"
        )
    runtime.check_cancelled()
    candidate_fingerprint = _fingerprint(messages)
    if candidate_fingerprint == control.candidate_fingerprint:
        raise _failure("compaction_no_progress")
    control.candidate_fingerprint = candidate_fingerprint
    record = CompactionRecord(
        summary=summary,
        covered_through_uuid=prefix[-1].uuid,
        tokens_before=estimate_request_tokens(assemble(history), runtime),
        tokens_after=assessment.tokens,
        model=runtime.model_id,
        checkpoint_version=4 if anchor is not None else 3,
        summary_prompt_version=prompt_version,
        summary_source_from_uuid=prefix[0].uuid,
        retained_from_uuid=tail[0].uuid if tail else "",
        omitted_before_count=0,
        effective_window=runtime.budget.effective_window,
        calibration_factor=runtime.calibration_factor,
        trigger_reason=runtime.trigger_reason,
        summary_attempts=control.summary_requests,
        tool_results_truncated=projection.truncated,
        activity_id=activity_id,
        scope="model",
        summary_message_uuid=summary_uuid,
        model_prefix_sha256=checkpoint_prefix_digest(message.uuid for message in prefix),
        task_anchor_uuid=anchor.uuid if anchor else "",
        source_user_event_uuid=str(anchor.meta["source_user_event_uuid"]) if anchor else "",
        source_user_sha256=str(anchor.meta["source_user_sha256"]) if anchor else "",
        task_anchor_sha256=anchor_content_digest(anchor) if anchor else "",
        task_anchor_version=TASK_ANCHOR_VERSION if anchor else 0,
    )
    return BuildResult(
        messages=messages,
        new_compaction=record,
        new_tool_context_edit=new_tool_context_edit,
        notes={
            "context_strategy": "summarizing",
            "compacted": True,
            "summary_input": "sequential_full",
            "token_estimate": assessment.tokens,
            "summary_attempts": control.summary_requests,
            "summary_segments": control.successful_segments,
            "summary_target_tokens": target_tokens,
            "summary_output_retry_used": control.summary_output_retry_used,
            "task_internal_compaction": task_internal,
            "source_fingerprint": control.source_fingerprint,
            "tool_results_truncated": projection.truncated,
            "tool_results_omitted": 0,
            "tool_results_cleared": projection.cleared,
            "tool_result_cleared_tokens": projection.cleared_tokens,
            "final_strategy": "summary",
        },
    )
