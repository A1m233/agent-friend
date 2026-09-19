"""Request-bound input usage with conservative reuse of unchanged content.

Persist hashes and counts, never another copy of model-visible content. Output
usage only bounds matching replayed reasoning, never unconditionally adds input.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from .budget import (
    CHARS_TO_TOKENS,
    DOCUMENT_ATTACHMENT_TOKEN_CAP,
    IMAGE_ATTACHMENT_TOKENS,
    TOOL_IMAGE_TOKENS,
    estimate_tool_schema_tokens,
)
from .usage_components import components, valid_digest
from .usage_components import digest as digest


def estimate_rendered_tokens(
    messages: list[dict[str, Any]],
    *,
    file_tokens: int = DOCUMENT_ATTACHMENT_TOKEN_CAP,
) -> int:
    """Retain the existing character heuristic, including replayed reasoning.

    Role/JSON framing does not change the established fallback policy. Tool-call
    structures do count, as in budget.estimate_tokens. Images use the established
    attachment allowance rather than their encoded payload length.
    """

    def cost(value: Any, image_tokens: int = IMAGE_ATTACHMENT_TOKENS) -> float:
        if value is None:
            return 0
        if isinstance(value, str):
            return len(value) * CHARS_TO_TOKENS
        if isinstance(value, dict):
            if value.get("type") in {"file", "input_file"}:
                return float(file_tokens)
            if value.get("type") in {"image_url", "input_image"}:
                return float(image_tokens)
            if "text" in value:
                return cost(value["text"])
            return len(str(value)) * CHARS_TO_TOKENS
        if isinstance(value, list):
            return sum(cost(item, image_tokens) for item in value)
        return len(str(value)) * CHARS_TO_TOKENS

    return int(
        sum(
            cost(
                message.get("content"),
                TOOL_IMAGE_TOKENS if message.get("role") == "tool" else IMAGE_ATTACHMENT_TOKENS,
            )
            + (
                len(str(message["tool_calls"])) * CHARS_TO_TOKENS
                if message.get("tool_calls")
                else 0
            )
            + cost(message.get("reasoning_content"))
            + cost(message.get("reasoning_details"))
            for message in messages
        )
    )


@dataclass(frozen=True)
class RequestMeasurement:
    scope: str
    prefix: str
    message_count: int
    compatibility_scope: str | None = None
    component_digests: tuple[str, ...] = ()

    @classmethod
    def capture(
        cls,
        messages: list[dict[str, Any]],
        scope: str,
        *,
        compatibility_scope: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> RequestMeasurement:
        return cls(
            scope,
            digest(messages),
            len(messages),
            compatibility_scope,
            tuple(key for key, _ in components(messages, tools)) if compatibility_scope else (),
        )


@dataclass(frozen=True)
class UsageAnchor:
    scope: str
    prefix: str
    message_count: int
    input_tokens: int
    version: int = 2
    output_reasoning_digest: str | None = None
    output_reasoning_tokens: int | None = None
    compatibility_scope: str | None = None
    component_digests: tuple[str, ...] = ()

    @classmethod
    def from_usage(
        cls, request: RequestMeasurement, usage: Any, *, reasoning_details: Any = None
    ) -> UsageAnchor | None:
        # The provider layer normalizes inclusive totals and exclusive buckets
        # into prompt_tokens. Neither completion nor cache-hit subtraction belongs here.
        # Unknown counters must not silently become trusted, even if positive.
        if usage is None or usage.input_token_accounting not in {
            "inclusive_total",
            "exclusive_components",
        }:
            return None
        tokens = usage.prompt_tokens
        if type(tokens) is not int or tokens <= 0:
            return None
        for value in (usage.cached_prompt_tokens, usage.cache_write_prompt_tokens):
            if value is not None and (type(value) is not int or not 0 <= value <= tokens):
                return None
        if (usage.cached_prompt_tokens or 0) + (usage.cache_write_prompt_tokens or 0) > tokens:
            return None
        # Output usage is not additional input by itself. It is only an upper
        # bound for the exact reasoning payload if it is replayed in the delta.
        # This avoids estimating hidden reasoning by its ID/ciphertext length.
        output = usage.completion_tokens
        has_bound = bool(reasoning_details) and type(output) is int and output > 0
        return cls(
            request.scope,
            request.prefix,
            request.message_count,
            tokens,
            version=4 if request.compatibility_scope is not None else 2,
            output_reasoning_digest=digest(reasoning_details) if has_bound else None,
            output_reasoning_tokens=output if has_bound else None,
            compatibility_scope=request.compatibility_scope,
            component_digests=request.component_digests,
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def restore(cls, value: Any) -> UsageAnchor | None:
        if not isinstance(value, dict) or type(value.get("version")) is not int:
            return None
        if value["version"] not in {1, 2, 3, 4}:
            return None
        for key in ("scope", "prefix"):
            field = value.get(key)
            if not isinstance(field, str) or len(field) != 64:
                return None
            if any(char not in "0123456789abcdef" for char in field):
                return None
        for key in ("message_count", "input_tokens"):
            if type(value.get(key)) is not int or value[key] <= 0:
                return None
        reasoning_digest = value.get("output_reasoning_digest") if value["version"] >= 2 else None
        reasoning_tokens = value.get("output_reasoning_tokens") if value["version"] >= 2 else None
        if (reasoning_digest is not None or reasoning_tokens is not None) and (
            not isinstance(reasoning_digest, str)
            or len(reasoning_digest) != 64
            or any(char not in "0123456789abcdef" for char in reasoning_digest)
            or type(reasoning_tokens) is not int
            or reasoning_tokens <= 0
        ):
            return None
        compatibility_scope = value.get("compatibility_scope") if value["version"] >= 3 else None
        keys = value.get("component_digests") if value["version"] >= 3 else ()
        if value["version"] >= 3 and (
            not valid_digest(compatibility_scope)
            or not isinstance(keys, (list, tuple))
            or not keys
            or not all(valid_digest(key) for key in keys)
        ):
            return None
        return cls(
            value["scope"],
            value["prefix"],
            value["message_count"],
            value["input_tokens"],
            version=value["version"] if value["version"] >= 3 else 2,
            output_reasoning_digest=reasoning_digest,
            output_reasoning_tokens=reasoning_tokens,
            compatibility_scope=compatibility_scope,
            component_digests=tuple(keys or ()),
        )


def _estimate_delta(messages: list[dict[str, Any]], anchor: UsageAnchor) -> int | None:
    """Count only replayed output; unknown opaque deltas cannot spend an anchor."""
    tokens = 0
    for message in messages:
        details = message.get("reasoning_details")
        if not details:
            tokens += estimate_rendered_tokens([message])
            continue
        if (
            anchor.output_reasoning_digest == digest(details)
            and anchor.output_reasoning_tokens is not None
        ):
            # The output bound includes all reasoning items from this response.
            # Visible text/tool calls are estimated separately (conservative).
            tokens += anchor.output_reasoning_tokens
            tokens += estimate_rendered_tokens([{**message, "reasoning_details": None}])
        else:
            # Text-only reasoning is countable. A summary, signature, encrypted
            # payload or reasoning ID is not evidence for its underlying cost.
            if not isinstance(details, (list, tuple)) or any(
                not isinstance(item, dict)
                or item.get("encrypted_content")
                or not item.get("content")
                or not isinstance(item["content"], list)
                or any(
                    not isinstance(part, dict)
                    or part.get("type") != "reasoning_text"
                    or not isinstance(part.get("text"), str)
                    for part in item["content"]
                )
                for item in details
            ):
                return None
            tokens += estimate_rendered_tokens([message])
    return tokens


@dataclass(frozen=True)
class InputEstimate:
    tokens: int
    mode: str
    reason: str
    anchor_tokens: int = 0
    covered_messages: int = 0
    delta_tokens: int = 0
    reused_components: int = 0
    added_components: int = 0
    retained_components: int = 0
    unknown_tokens: int = 0
    unknown_components: int = 0

    def notes(self) -> dict[str, Any]:
        return {
            "token_estimate": self.tokens,
            "estimation_mode": self.mode,
            "anchor_fallback_reason": self.reason,
            "anchor_input_tokens": self.anchor_tokens,
            "anchor_covered_messages": self.covered_messages,
            "incremental_tokens": self.delta_tokens,
            "reused_components": self.reused_components,
            "added_components": self.added_components,
            "retained_components": self.retained_components,
            "unknown_input_tokens": self.unknown_tokens,
            "unknown_input_components": self.unknown_components,
            "input_cost_known": self.unknown_components == 0,
        }


def estimate_input(
    messages: list[dict[str, Any]],
    *,
    scope: str,
    anchor: UsageAnchor | None,
    fallback_tokens: int | Callable[[], int],
    compatibility_scope: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    unknown_token_reserve: int = 1_000_000,
    fallback_threshold: int | None = None,
) -> InputEstimate:
    def full_estimate(reason: str, compatible: UsageAnchor | None = None) -> InputEstimate:
        fallback = fallback_tokens() if callable(fallback_tokens) else fallback_tokens
        bound = compatible or UsageAnchor("", "", 0, 0)
        remaining = Counter(bound.component_digests)
        unknown = 0
        for key, payload in components(messages, tools):
            if remaining[key] > 0:
                remaining[key] -= 1
            elif "_tool_schema" not in payload and _estimate_delta([payload], bound) is None:
                unknown += 1
        return InputEstimate(
            fallback,
            "full_estimate",
            reason,
            unknown_tokens=unknown_token_reserve if unknown else 0,
            unknown_components=unknown,
        )

    reason = "no_trusted_usage"
    if anchor is not None:
        if anchor.version >= 3:
            if anchor.compatibility_scope != compatibility_scope:
                return full_estimate("request_scope_changed")
            return _estimate_components(
                messages, tools, anchor, fallback_tokens, unknown_token_reserve, fallback_threshold
            )
        reason = "request_scope_changed"
        if anchor.scope == scope:
            reason = "request_prefix_changed"
            if (
                len(messages) >= anchor.message_count
                and digest(messages[: anchor.message_count]) == anchor.prefix
            ):
                delta = _estimate_delta(messages[anchor.message_count :], anchor)
                if delta is not None:
                    return InputEstimate(
                        anchor.input_tokens + delta,
                        "usage_anchor",
                        "none",
                        anchor.input_tokens,
                        anchor.message_count,
                        delta,
                    )
                reason = "unmeasured_reasoning_delta"
    return full_estimate(reason, anchor if anchor is not None and anchor.scope == scope else None)


def _estimate_components(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    anchor: UsageAnchor,
    fallback_tokens: int | Callable[[], int],
    unknown_token_reserve: int,
    fallback_threshold: int | None,
) -> InputEstimate:
    remaining = Counter(anchor.component_digests)
    added = reused = delta = unknown = 0
    for key, payload in components(messages, tools):
        if remaining[key] > 0:
            remaining[key] -= 1
            reused += 1
            continue
        added += 1
        if "_tool_schema" in payload:
            delta += estimate_tool_schema_tokens([payload["_tool_schema"]])
            continue
        cost = _estimate_delta([payload], anchor)
        if cost is None:
            # Ciphertext length is not a bound on the underlying reasoning.
            # This is unknown, not another measured full context window.
            unknown += 1
        else:
            delta += cost
    retained = sum(remaining.values())
    reserve = unknown_token_reserve if unknown else 0
    tokens = anchor.input_tokens + delta + reserve
    mode = "usage_anchor" if not retained else "usage_anchor_partial"
    reason = "unmeasured_reasoning_delta" if unknown else "none"
    # A shorter candidate must be able to shed retained slack after compaction
    # or FIFO trimming. Never subtract an estimated old fragment from real usage.
    if not unknown and (
        len(messages) < anchor.message_count
        or (retained and fallback_threshold is not None and tokens > fallback_threshold)
    ):
        fallback = fallback_tokens() if callable(fallback_tokens) else fallback_tokens
        if fallback < tokens:
            return InputEstimate(fallback, "full_estimate", "rewritten_candidate")
    return InputEstimate(
        tokens,
        mode,
        reason,
        anchor.input_tokens,
        0,
        delta + reserve,
        reused,
        added,
        retained,
        reserve,
        unknown,
    )


def estimate_full_input(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    unknown_token_reserve: int,
    anchor: UsageAnchor | None = None,
) -> int:
    """Independent candidate estimate without pricing opaque reasoning ciphertext.

    A compatible input measurement bounds all retained opaque components together;
    an output measurement bounds its matching reasoning. Neither is a per-item
    exact count. Unmeasured opaque content reserves a whole context window.
    """
    remaining = Counter(anchor.component_digests if anchor is not None else ())
    empty = UsageAnchor("", "", 0, 0)
    bound = anchor or empty
    tokens = 0
    retained_opaque = False
    unknown = False
    for key, payload in components(messages, tools):
        matched = remaining[key] > 0
        if matched:
            remaining[key] -= 1
        if "_tool_schema" in payload:
            tokens += estimate_tool_schema_tokens([payload["_tool_schema"]])
            continue
        cost = _estimate_delta([payload], bound)
        if cost is not None:
            tokens += cost
        elif matched:
            retained_opaque = True
        else:
            unknown = True
    if retained_opaque:
        tokens += bound.input_tokens
    if unknown:
        # One fail-closed admission reserve for the request, not a token estimate
        # multiplied by the number of opaque components.
        tokens += unknown_token_reserve
    return tokens
