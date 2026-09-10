"""Request-bound input usage; only an unchanged prefix can spend a measured anchor.

Persist hashes and counts, never another copy of model-visible content. Completion
tokens are deliberately excluded: only replayed assistant items belong to input.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from .budget import CHARS_TO_TOKENS, DOCUMENT_ATTACHMENT_TOKEN_CAP, IMAGE_ATTACHMENT_TOKENS


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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

    def cost(value: Any) -> float:
        if value is None:
            return 0
        if isinstance(value, str):
            return len(value) * CHARS_TO_TOKENS
        if isinstance(value, dict):
            if value.get("type") in {"file", "input_file"}:
                return float(file_tokens)
            if value.get("type") in {"image_url", "input_image"}:
                return float(IMAGE_ATTACHMENT_TOKENS)
            if "text" in value:
                return cost(value["text"])
            return len(str(value)) * CHARS_TO_TOKENS
        if isinstance(value, list):
            return sum(cost(item) for item in value)
        return len(str(value)) * CHARS_TO_TOKENS

    return int(
        sum(
            cost(message.get("content"))
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

    @classmethod
    def capture(cls, messages: list[dict[str, Any]], scope: str) -> RequestMeasurement:
        return cls(scope, digest(messages), len(messages))


@dataclass(frozen=True)
class UsageAnchor:
    scope: str
    prefix: str
    message_count: int
    input_tokens: int
    version: int = 1

    @classmethod
    def from_usage(cls, request: RequestMeasurement, usage: Any) -> UsageAnchor | None:
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
        return cls(request.scope, request.prefix, request.message_count, tokens)

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def restore(cls, value: Any) -> UsageAnchor | None:
        if not isinstance(value, dict) or type(value.get("version")) is not int:
            return None
        if value["version"] != 1:
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
        return cls(value["scope"], value["prefix"], value["message_count"], value["input_tokens"])


@dataclass(frozen=True)
class InputEstimate:
    tokens: int
    mode: str
    reason: str
    anchor_tokens: int = 0
    covered_messages: int = 0
    delta_tokens: int = 0

    def notes(self) -> dict[str, Any]:
        return {
            "token_estimate": self.tokens,
            "estimation_mode": self.mode,
            "anchor_fallback_reason": self.reason,
            "anchor_input_tokens": self.anchor_tokens,
            "anchor_covered_messages": self.covered_messages,
            "incremental_tokens": self.delta_tokens,
        }


def estimate_input(
    messages: list[dict[str, Any]],
    *,
    scope: str,
    anchor: UsageAnchor | None,
    fallback_tokens: int | Callable[[], int],
) -> InputEstimate:
    reason = "no_trusted_usage"
    if anchor is not None:
        reason = "request_scope_changed"
        if anchor.scope == scope:
            reason = "request_prefix_changed"
            if (
                len(messages) >= anchor.message_count
                and digest(messages[: anchor.message_count]) == anchor.prefix
            ):
                delta = estimate_rendered_tokens(messages[anchor.message_count :])
                return InputEstimate(
                    anchor.input_tokens + delta,
                    "usage_anchor",
                    "none",
                    anchor.input_tokens,
                    anchor.message_count,
                    delta,
                )
    fallback = fallback_tokens() if callable(fallback_tokens) else fallback_tokens
    return InputEstimate(fallback, "full_estimate", reason)
