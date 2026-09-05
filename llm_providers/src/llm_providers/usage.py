"""Provider response usage normalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .stream_events import LLMUsage


def usage_field(value: Any, name: str) -> Any:
    """Read a usage field from provider objects and raw dictionaries alike."""

    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _first_usage_int_with_source(value: Any, *names: str) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    for name in names:
        raw = usage_field(value, name)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and raw >= 0:
            return int(raw), name
    return None, None


def _normalize_usage(usage: Any, *, usage_source: str | None = None) -> LLMUsage | None:
    if usage is None:
        return None
    prompt, prompt_source = _first_usage_int_with_source(usage, "prompt_tokens")
    completion, completion_source = _first_usage_int_with_source(
        usage,
        "completion_tokens",
        "output_tokens",
    )
    total, total_source = _first_usage_int_with_source(usage, "total_tokens")
    provider_input, provider_input_source = _first_usage_int_with_source(usage, "input_tokens")
    # Prefer provider-native/public counters over LiteLLM's private compatibility
    # attributes.  LiteLLM's ``Usage`` object initializes
    # ``_cache_read_input_tokens`` to zero even when an OpenAI-compatible
    # provider reports the real value under ``prompt_tokens_details.cached_tokens``.
    # Reading the private zero first therefore turns a real hit into a reported
    # miss.  Native DeepSeek/Kimi counters remain authoritative when present.
    cached, cached_source = _first_usage_int_with_source(
        usage,
        "prompt_cache_hit_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
    )
    if cached is None:
        prompt_details = usage_field(usage, "prompt_tokens_details")
        cached, detail_source = _first_usage_int_with_source(prompt_details, "cached_tokens")
        if detail_source is not None:
            cached_source = f"prompt_tokens_details.{detail_source}"
    if cached is None:
        cached, cached_source = _first_usage_int_with_source(
            usage,
            "_cache_read_input_tokens",
        )
    cache_write, cache_write_source = _first_usage_int_with_source(
        usage,
        "prompt_cache_write_tokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
    )
    if cache_write is None:
        prompt_details = usage_field(usage, "prompt_tokens_details")
        cache_write, detail_source = _first_usage_int_with_source(
            prompt_details,
            "cache_write_tokens",
            "cache_creation_tokens",
        )
        if detail_source is not None:
            cache_write_source = f"prompt_tokens_details.{detail_source}"
    if cache_write is None:
        input_details = usage_field(usage, "input_tokens_details")
        cache_write, detail_source = _first_usage_int_with_source(
            input_details,
            "cache_write_tokens",
            "cache_creation_tokens",
        )
        if detail_source is not None:
            cache_write_source = f"input_tokens_details.{detail_source}"
    if cache_write is None:
        cache_write, cache_write_source = _first_usage_int_with_source(
            usage,
            "_cache_creation_input_tokens",
        )
    cache_miss, cache_miss_source = _first_usage_int_with_source(usage, "prompt_cache_miss_tokens")
    if (
        prompt is None
        and completion is None
        and total is None
        and provider_input is None
        and cached is None
        and cache_write is None
        and cache_miss is None
    ):
        return None
    input_other: int | None = None
    input_other_source: str | None = None
    input_other_derived = False
    accounting = "partial"

    # Anthropic-style usage reports the non-cached suffix in ``input_tokens`` and
    # exposes cache read/write as exclusive components.  It must be expanded into
    # an inclusive total before the common contract is populated.
    if prompt is None and provider_input is not None:
        input_other = provider_input
        input_other_source = provider_input_source
        prompt = provider_input + (cached or 0) + (cache_write or 0)
        prompt_source = "derived_exclusive_input_components"
        accounting = "exclusive_components"
    elif prompt is None and cached is not None and cache_miss is not None:
        prompt = cached + cache_miss
        prompt_source = "derived_cache_hit_plus_miss"
        accounting = "exclusive_components"
    elif prompt is not None:
        accounting = "inclusive_total"

    prompt_tokens = int(prompt or 0)
    if cache_miss is not None:
        input_other = cache_miss
        input_other_source = cache_miss_source
    elif (
        prompt is not None
        and cached is not None
        and cache_write is not None
        and cached + cache_write <= prompt_tokens
    ):
        input_other = prompt_tokens - cached - cache_write
        input_other_source = "derived_input_total_minus_cache_read_write"
        input_other_derived = True

    cache_miss_derived = False
    if cache_miss is None and cached is not None:
        cache_miss = max(0, prompt_tokens - cached)
        cache_miss_source = "derived_prompt_minus_cached"
        cache_miss_derived = True
    if prompt_source is None:
        accounting = "unavailable"
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=int(completion or 0),
        total_tokens=int(total or 0),
        cached_prompt_tokens=cached,
        cache_write_prompt_tokens=cache_write,
        input_other_prompt_tokens=input_other,
        cache_miss_prompt_tokens=cache_miss,
        usage_source=usage_source,
        input_total_prompt_tokens_source=prompt_source,
        completion_tokens_source=completion_source,
        total_tokens_source=total_source,
        cached_prompt_tokens_source=cached_source,
        cache_write_prompt_tokens_source=cache_write_source,
        input_other_prompt_tokens_source=input_other_source,
        cache_miss_prompt_tokens_source=cache_miss_source,
        input_token_accounting=accounting,
        input_other_prompt_tokens_derived=input_other_derived,
        cache_miss_prompt_tokens_derived=cache_miss_derived,
    )


def extract_usage(response: Any) -> LLMUsage | None:
    """Normalize top-level usage from a non-streaming response or chunk."""

    return _normalize_usage(usage_field(response, "usage"), usage_source="top_level")


def extract_stream_usage(chunk: Any) -> LLMUsage | None:
    """Normalize top-level usage or Kimi's provider-specific choice usage."""

    usage = extract_usage(chunk)
    if usage is not None:
        return usage
    choices = usage_field(chunk, "choices") or []
    for choice in choices:
        choice_usage = usage_field(choice, "usage")
        if choice_usage is None:
            model_dump = getattr(choice, "model_dump", None)
            if callable(model_dump):
                dumped = model_dump()
                if isinstance(dumped, dict):
                    choice_usage = dumped.get("usage")
        usage = _normalize_usage(choice_usage, usage_source="choice")
        if usage is not None:
            return usage
    return None


def extract_responses_usage(response: Any) -> LLMUsage | None:
    """Responses input_tokens is inclusive; never add cache buckets to it."""
    usage = usage_field(response, "usage")
    if usage is None:
        return None
    details = usage_field(usage, "input_tokens_details")
    normalized = _normalize_usage(
        {
            "prompt_tokens": usage_field(usage, "input_tokens"),
            "completion_tokens": usage_field(usage, "output_tokens"),
            "total_tokens": usage_field(usage, "total_tokens"),
            "prompt_tokens_details": {
                "cached_tokens": usage_field(details, "cached_tokens"),
                "cache_write_tokens": usage_field(details, "cache_write_tokens"),
            },
        },
        usage_source="responses",
    )
    if normalized is None:
        return None
    return replace(
        normalized,
        input_total_prompt_tokens_source=(
            "input_tokens" if normalized.input_total_prompt_tokens_source else None
        ),
        completion_tokens_source="output_tokens" if normalized.completion_tokens_source else None,
        cached_prompt_tokens_source=(
            "input_tokens_details.cached_tokens" if normalized.cached_prompt_tokens_source else None
        ),
        cache_write_prompt_tokens_source=(
            "input_tokens_details.cache_write_tokens"
            if normalized.cache_write_prompt_tokens_source
            else None
        ),
    )


__all__ = ["extract_responses_usage", "extract_stream_usage", "extract_usage", "usage_field"]
