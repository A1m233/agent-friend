from __future__ import annotations

from typing import Any

from agent.prompt_cache_trace import PromptCacheTraceTracker, PromptRequestTrace


def _begin(
    tracker: PromptCacheTraceTracker,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> PromptRequestTrace:
    return tracker.begin(
        messages=messages,
        tools=tools,
        provider_model="openai/kimi-k2.6",
        api_base="https://api.moonshot.cn/v1",
        prompt_cache_key="session-1",
    )


def test_appended_suffix_preserves_previous_request_prefix_without_logging_content() -> None:
    tracker = PromptCacheTraceTracker()
    secret = "PRIVATE_USER_TEXT"
    tools = [{"type": "function", "function": {"name": "weather_query"}}]
    first_messages: list[dict[str, Any]] = [
        {"role": "system", "content": "stable system"},
        {"role": "user", "content": secret},
    ]
    first = _begin(tracker, first_messages, tools=tools)
    tracker.finish(first, cache_state="reported_nonzero")

    second = _begin(
        tracker,
        [
            *first_messages,
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow-up"},
        ],
        tools=tools,
    )

    assert second.lineage_state == "continuous"
    assert second.previous_request_prefix_preserved is True
    assert second.common_prefix_messages == len(first_messages)
    assert second.common_prefix_estimated_tokens is not None
    assert second.tool_schema_preserved is True
    assert secret not in str(second.log_fields())
    assert secret not in str(tracker.finish(second, cache_state="reported_zero"))


def test_tool_schema_change_marks_prefix_as_not_preserved() -> None:
    tracker = PromptCacheTraceTracker()
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]
    first = _begin(
        tracker,
        messages,
        tools=[{"type": "function", "function": {"name": "old_tool"}}],
    )
    tracker.finish(first, cache_state="reported_nonzero")

    second = _begin(
        tracker,
        messages,
        tools=[{"type": "function", "function": {"name": "new_tool"}}],
    )

    assert second.common_prefix_messages == 1
    assert second.tool_schema_preserved is False
    assert second.previous_request_prefix_preserved is False
    assert second.first_divergence_index is None


def test_restored_lineage_reports_idle_time_without_claiming_prefix_continuity() -> None:
    tracker = PromptCacheTraceTracker()
    seed = _begin(tracker, [{"role": "user", "content": "old"}])
    durable = tracker.finish(seed, cache_state="unavailable")

    restored = PromptCacheTraceTracker()
    restored.restore(
        durable,
        finished_at_unix_ms=int(durable["finished_at_unix_ms"]),
    )
    current = _begin(restored, [{"role": "user", "content": "new"}])

    assert current.lineage_state == "restored_without_prefix"
    assert current.cache_idle_ms is not None
    assert current.previous_cache_state == "unavailable"
    assert current.previous_request_prefix_preserved is None
