"""Versioned, content-free prompt-cache observations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from .protocol import ModelCallControl, ModelCallTraceContext
from .stream_events import LLMResponseDiagnostics, LLMUsage

PROMPT_CACHE_OBSERVATION_SCHEMA = "agent-friend.prompt-cache-observation"
PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION = 2
PROMPT_CACHE_OBSERVATION_LOG_PREFIX = "prompt_cache_observation "

logger = logging.getLogger(__name__)

_BUILD_IDENTITY_ENV = "AGENT_FRIEND_BUILD_IDENTITY_JSON"

_SAFE_ADAPTER_TRACE_FIELDS = frozenset(
    {
        "namespace",
        "prompt_fingerprint",
        "system_fingerprint",
        "tool_schema_fingerprint",
        "lineage_state",
        "cache_idle_ms",
        "previous_cache_state",
        "previous_request_prefix_preserved",
        "common_prefix_messages",
        "common_prefix_estimated_tokens",
        "first_divergence_index",
        "first_divergence_role",
        "tool_schema_preserved",
        "prompt_payload_bytes",
        "total_message_chars",
        "tool_result_count",
        "tool_result_chars",
        "max_tool_result_chars",
    }
)


def _safe_ref(kind: str, value: str | None) -> str | None:
    if not value:
        return None
    digest = hashlib.sha256(f"prompt-cache-observation:v1:{kind}:{value}".encode()).hexdigest()
    return digest[:16]


@lru_cache(maxsize=1)
def _runtime_code_identity() -> dict[str, Any]:
    raw = os.environ.get(_BUILD_IDENTITY_ENV, "").strip()
    if not raw:
        return {
            "mode": "source",
            "collector": "llm-providers",
            "collector_revision": PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
        }
    identity_ref = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"mode": "packaged_invalid", "identity_ref": identity_ref}
    if not isinstance(value, Mapping):
        return {"mode": "packaged_invalid", "identity_ref": identity_ref}
    return {
        "mode": "packaged",
        "identity_ref": identity_ref,
        "build_ref": _safe_ref("build", _text(value.get("buildId"))),
        "profile": _text(value.get("profileId")),
        "product_version": _text(value.get("productVersion")),
        "source_commit": _text(value.get("sourceCommit")),
        "collector": "llm-providers",
        "collector_revision": PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
    }


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _safe_adapter_trace(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    safe: dict[str, Any] = {}
    for key in _SAFE_ADAPTER_TRACE_FIELDS:
        item = value.get(key)
        if item is None:
            continue
        if isinstance(item, (str, int, float, bool)):
            safe[key] = item
    return safe or None


_CHANGE_FLAG_FIELDS = frozenset(
    {
        "system_changed",
        "persona_changed",
        "tools_changed",
        "skills_changed",
        "messages_changed",
        "messages_appended",
        "messages_rewritten",
        "runtime_changed",
        "settings_changed",
        "model_route_changed",
    }
)


def _safe_comparison(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    safe: dict[str, Any] = {}
    for key in (
        "previous_item_count",
        "previous_input_tokens",
        "previous_response_model",
        "first_divergence_domain",
        "first_divergence_index",
        "first_divergence_role",
        "coarse_relation",
    ):
        item = value.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            safe[key] = item
    raw_flags = value.get("change_flags")
    if isinstance(raw_flags, Mapping):
        safe["change_flags"] = {
            key: raw_flags.get(key)
            for key in _CHANGE_FLAG_FIELDS
            if raw_flags.get(key) is None or isinstance(raw_flags.get(key), bool)
        }
    return safe


def _comparison_relation(
    *,
    scope: str,
    precision: str,
    wire_relation: str,
    comparison: Mapping[str, Any],
) -> str:
    if scope == "cross_user_turn":
        if precision == "exact_item":
            return "unavailable" if wire_relation == "baseline" else wire_relation
        return _text(comparison.get("coarse_relation")) or "unavailable_precision_reduced"
    if scope == "cross_process_resume":
        return _text(comparison.get("coarse_relation")) or "unavailable_precision_reduced"
    if scope == "unavailable":
        return "unavailable"
    return wire_relation


def _lost_prefix_fields(
    *,
    scope: str,
    relation: str,
    first_domain: str | None,
    diagnostics: LLMResponseDiagnostics | None,
    comparison: Mapping[str, Any],
) -> tuple[int | None, int | None]:
    if scope != "cross_user_turn" or diagnostics is None:
        return None, None
    previous_items = diagnostics.previous_request_items
    if previous_items is None:
        previous_items = _nonnegative_int(comparison.get("previous_item_count"))
    previous_tokens = _nonnegative_int(comparison.get("previous_input_tokens"))
    if relation in {"strict_extension", "same_request_retry"}:
        return 0, 0
    if relation != "rebase":
        return None, None

    lost_items = diagnostics.lost_prefix_items
    if first_domain in {"model-route", "settings", "tools"}:
        lost_items = previous_items
    if previous_tokens is None or previous_items is None or previous_items <= 0:
        return lost_items, None
    if lost_items is None:
        return None, None
    estimate = round(previous_tokens * min(lost_items, previous_items) / previous_items)
    return lost_items, estimate


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def classify_prompt_cache_result(
    relation: str,
    cache_read_tokens: int | None,
) -> str:
    """Combine request continuity and provider cache-read facts without guessing causes."""

    if relation == "baseline":
        return "baseline_request"
    if relation == "same_request_retry":
        return "transport_retry_result"
    if relation == "strict_extension":
        if cache_read_tokens is None:
            return "structure_preserved_usage_unavailable"
        if cache_read_tokens > 0:
            return "structure_preserved_cache_read"
        return "structure_preserved_no_cache_read"
    if relation == "rebase":
        return "structure_changed"
    if cache_read_tokens is None:
        return "insufficient_observation"
    if cache_read_tokens > 0:
        return "cache_read_structure_unavailable"
    return "no_cache_read_structure_unavailable"


def _usage_payload(usage: LLMUsage | None) -> dict[str, Any]:
    if usage is None:
        return {
            "input_total": None,
            "cache_read": None,
            "cache_write": None,
            "input_other": None,
            "output": None,
            "accounting": "unavailable",
            "buckets_reconciled": False,
            "sources": {},
            "derived": {},
        }
    input_total = (
        usage.prompt_tokens
        if usage.prompt_tokens > 0
        or usage.input_total_prompt_tokens_source is not None
        or usage.input_token_accounting != "unavailable"
        else None
    )
    output = (
        usage.completion_tokens
        if usage.completion_tokens > 0 or usage.completion_tokens_source is not None
        else None
    )
    return {
        "input_total": input_total,
        "cache_read": usage.cached_prompt_tokens,
        "cache_write": usage.cache_write_prompt_tokens,
        "input_other": usage.input_other_prompt_tokens,
        "output": output,
        "accounting": usage.input_token_accounting,
        "buckets_reconciled": usage.input_buckets_reconciled,
        "sources": {
            "usage": usage.usage_source,
            "input_total": usage.input_total_prompt_tokens_source,
            "output": usage.completion_tokens_source,
            "cache_read": usage.cached_prompt_tokens_source,
            "cache_write": usage.cache_write_prompt_tokens_source,
            "input_other": usage.input_other_prompt_tokens_source,
        },
        "derived": {
            "input_other": usage.input_other_prompt_tokens_derived,
            "legacy_cache_miss": usage.cache_miss_prompt_tokens_derived,
        },
    }


def build_prompt_cache_observation(
    *,
    requested_model: str,
    provider_id: str | None,
    message_count: int,
    tool_count: int,
    usage: LLMUsage | None,
    diagnostics: LLMResponseDiagnostics | None,
    control: ModelCallControl | None = None,
    total_elapsed_ms: int | None = None,
    status: str = "completed",
    error_type: str | None = None,
) -> dict[str, Any]:
    """Build one JSON-compatible observation without serializing request content."""

    control = control or ModelCallControl()
    trace = control.trace_context or ModelCallTraceContext()
    wire_relation = diagnostics.request_relation if diagnostics is not None else "unavailable"
    comparison_scope = trace.comparison_scope
    comparison_precision = trace.comparison_precision
    comparison = _safe_comparison(trace.comparison)
    final_wire_relation = wire_relation
    if wire_relation == "baseline" and comparison_scope in {
        "cross_user_turn",
        "cross_process_resume",
        "unavailable",
    }:
        final_wire_relation = "unavailable"
    cross_turn_relation = _comparison_relation(
        scope=comparison_scope,
        precision=comparison_precision,
        wire_relation=wire_relation,
        comparison=comparison,
    )
    change_flags = dict(comparison.get("change_flags", {}))
    previous_response_model = _text(comparison.get("previous_response_model"))
    current_response_model = diagnostics.response_model if diagnostics is not None else None
    if previous_response_model and current_response_model:
        change_flags["model_route_changed"] = (
            previous_response_model != current_response_model
        ) or change_flags.get("model_route_changed") is True
    first_divergence_domain = _text(comparison.get("first_divergence_domain"))
    if change_flags.get("model_route_changed") is True:
        first_divergence_domain = "model-route"
    if first_divergence_domain is None and diagnostics is not None:
        first_divergence_domain = diagnostics.first_divergence_domain
    first_divergence_index = diagnostics.first_divergence_index if diagnostics is not None else None
    if first_divergence_index is None:
        first_divergence_index = _nonnegative_int(comparison.get("first_divergence_index"))
    first_divergence_role = (
        diagnostics.first_divergence_role if diagnostics is not None else None
    ) or _text(comparison.get("first_divergence_role"))
    lost_prefix_items, lost_prefix_tokens_estimate = _lost_prefix_fields(
        scope=comparison_scope,
        relation=cross_turn_relation,
        first_domain=first_divergence_domain,
        diagnostics=diagnostics,
        comparison=comparison,
    )
    cache_read = usage.cached_prompt_tokens if usage is not None else None
    return {
        "schema": PROMPT_CACHE_OBSERVATION_SCHEMA,
        "schema_revision": PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
        "observed_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "identity": {
            "operation": trace.operation,
            "surface": trace.surface,
            "run_ref": _safe_ref("run", trace.run_id),
            "session_ref": _safe_ref("session", trace.session_id),
            "call_kind": trace.call_kind,
            "call_index": trace.call_index,
            "turn_index": trace.turn_index,
            "recovery_attempt": trace.recovery_attempt,
            "message_count": message_count,
            "tool_count": tool_count,
            "code": _runtime_code_identity(),
        },
        "model": {
            "requested": requested_model,
            "response": diagnostics.response_model if diagnostics is not None else None,
            "provider": provider_id,
            "transport": diagnostics.transport if diagnostics is not None else "unknown",
            "service_tier": diagnostics.service_tier if diagnostics is not None else None,
        },
        "usage": _usage_payload(usage),
        "structure": {
            "final_wire_relation": final_wire_relation,
            "raw_final_wire_relation": wire_relation,
            "comparison_scope": comparison_scope,
            "comparison_precision": comparison_precision,
            "cross_turn_relation": cross_turn_relation,
            "common_prefix_items": (
                diagnostics.common_prefix_items if diagnostics is not None else None
            ),
            "first_divergence_domain": first_divergence_domain,
            "first_divergence_index": first_divergence_index,
            "first_divergence_role": first_divergence_role,
            "lost_prefix_items": lost_prefix_items,
            "lost_prefix_tokens_estimate": lost_prefix_tokens_estimate,
            "change_flags": change_flags,
            "request_bytes": diagnostics.request_bytes if diagnostics is not None else None,
            "continuation_disposition": control.continuation_disposition,
            "rebase_reason": control.rebase_reason,
            "adapter": _safe_adapter_trace(trace.adapter_trace),
        },
        "timing": {
            "stream_open_ms": diagnostics.stream_open_ms if diagnostics is not None else None,
            "first_chunk_ms": diagnostics.first_chunk_ms if diagnostics is not None else None,
            "first_reasoning_delta_ms": (
                diagnostics.first_reasoning_delta_ms if diagnostics is not None else None
            ),
            "total_ms": total_elapsed_ms,
        },
        "outcome": {
            "status": status,
            "error_type": error_type,
            "interpretation": classify_prompt_cache_result(
                final_wire_relation,
                cache_read,
            ),
            "cache_reporting_state": (
                usage.cache_reporting_state if usage is not None else "unavailable"
            ),
            "provider_request_ref": _safe_ref(
                "provider-request",
                diagnostics.provider_request_id if diagnostics is not None else None,
            ),
            "response_ref": _safe_ref(
                "provider-response",
                diagnostics.response_id if diagnostics is not None else None,
            ),
            "response_cost": diagnostics.response_cost if diagnostics is not None else None,
            "response_cost_source": (
                diagnostics.response_cost_source if diagnostics is not None else None
            ),
        },
    }


def emit_prompt_cache_observation(observation: Mapping[str, Any]) -> None:
    """Emit one compact, single-line observation through the governed log pipeline."""

    try:
        payload = json.dumps(
            observation,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        logger.info("%s%s", PROMPT_CACHE_OBSERVATION_LOG_PREFIX, payload)
    except Exception:
        logger.warning("prompt cache observation emission failed", exc_info=True)


__all__ = [
    "PROMPT_CACHE_OBSERVATION_LOG_PREFIX",
    "PROMPT_CACHE_OBSERVATION_SCHEMA",
    "PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION",
    "build_prompt_cache_observation",
    "classify_prompt_cache_result",
    "emit_prompt_cache_observation",
]
