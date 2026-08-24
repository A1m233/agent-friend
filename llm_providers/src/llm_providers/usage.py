"""Provider response usage normalization."""

from __future__ import annotations

from typing import Any

from .stream_events import LLMUsage


def usage_field(value: Any, name: str) -> Any:
    """Read a usage field from provider objects and raw dictionaries alike."""

    if isinstance(value, dict):
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
    prompt = usage_field(usage, "prompt_tokens")
    completion = usage_field(usage, "completion_tokens")
    total = usage_field(usage, "total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    prompt_tokens = int(prompt or 0)
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
    cache_miss, cache_miss_source = _first_usage_int_with_source(usage, "prompt_cache_miss_tokens")
    cache_miss_derived = False
    if cache_miss is None and cached is not None:
        cache_miss = max(0, prompt_tokens - cached)
        cache_miss_source = "derived_prompt_minus_cached"
        cache_miss_derived = True
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=int(completion or 0),
        total_tokens=int(total or 0),
        cached_prompt_tokens=cached,
        cache_miss_prompt_tokens=cache_miss,
        usage_source=usage_source,
        cached_prompt_tokens_source=cached_source,
        cache_miss_prompt_tokens_source=cache_miss_source,
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


__all__ = ["extract_stream_usage", "extract_usage", "usage_field"]
