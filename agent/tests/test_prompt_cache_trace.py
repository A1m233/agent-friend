from __future__ import annotations

import json
from typing import Any

import pytest
from agent.prompt_cache_trace import PromptCacheTraceTracker, PromptRequestTrace
from llm_providers.request_witness import witness_from_body

from llm_providers import ProviderRequestWitness


def _begin(
    tracker: PromptCacheTraceTracker,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    persona_id: str | None = None,
    settings: dict[str, Any] | None = None,
    model_route: dict[str, Any] | None = None,
    skills: dict[str, Any] | None = None,
    message_domains: tuple[str, ...] | None = None,
) -> PromptRequestTrace:
    return tracker.begin(
        messages=messages,
        message_domains=message_domains,
        tools=tools,
        provider_model="openai/kimi-k2.6",
        api_base="https://api.moonshot.cn/v1",
        prompt_cache_key="session-1",
        persona_id=persona_id,
        settings=settings,
        model_route=model_route,
        skills=skills,
    )


def _witness(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> ProviderRequestWitness:
    body = json.dumps({"messages": messages, "tools": tools or []}).encode()
    return witness_from_body(
        body,
        provider_id="openai-compatible",
        transport="litellm",
        transform_identity="test:v1",
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


def test_new_user_run_compares_with_previous_run_final_wire_witness() -> None:
    tracker = PromptCacheTraceTracker()
    tools = [{"type": "function", "function": {"name": "weather_query"}}]
    first_messages = [
        {"role": "system", "content": "stable system"},
        {"role": "user", "content": "first question"},
    ]
    first = _begin(
        tracker,
        first_messages,
        tools=tools,
        persona_id="persona-a",
        settings={"temperature": 0},
        model_route={"requested_model": "kimi"},
    )
    first_witness = _witness(first_messages, tools)
    tracker.finish(
        first,
        cache_state="reported_nonzero",
        run_id="run-a",
        witness=first_witness,
        input_tokens=100,
        response_model="kimi-k2.6",
    )

    second = _begin(
        tracker,
        [
            *first_messages,
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ],
        tools=tools,
        persona_id="persona-a",
        settings={"temperature": 0},
        model_route={"requested_model": "kimi"},
    )
    comparison = tracker.comparison_for(second, run_id="run-b")

    assert comparison.scope == "cross_user_turn"
    assert comparison.precision == "exact_item"
    assert comparison.previous_witness == first_witness
    assert comparison.previous_item_count == 2
    assert comparison.previous_input_tokens == 100
    assert comparison.change_flags is not None
    assert comparison.change_flags["messages_appended"] is True
    assert comparison.change_flags["messages_rewritten"] is False
    assert comparison.first_divergence_domain is None


@pytest.mark.parametrize(
    ("changed_domain", "expected_flag"),
    [
        ("persona", "persona_changed"),
        ("system", "system_changed"),
        ("runtime", "runtime_changed"),
        ("tools", "tools_changed"),
        ("settings", "settings_changed"),
        ("model-route", "model_route_changed"),
        ("messages", "messages_rewritten"),
    ],
)
def test_cross_user_run_classifies_breakpoint_domain(
    changed_domain: str,
    expected_flag: str,
) -> None:
    tracker = PromptCacheTraceTracker()
    base_messages = [
        {"role": "system", "content": "stable system"},
        {
            "role": "system",
            "content": "<agent_friend_runtime_context>stable</agent_friend_runtime_context>",
        },
        {"role": "user", "content": "stable history"},
    ]
    base_tools = [{"type": "function", "function": {"name": "old_tool"}}]
    first = _begin(
        tracker,
        base_messages,
        tools=base_tools,
        persona_id="persona-a",
        settings={"temperature": 0},
        model_route={"requested_model": "kimi-a"},
    )
    tracker.finish(
        first,
        cache_state="reported_zero",
        run_id="run-a",
        witness=_witness(base_messages, base_tools),
    )

    current_messages = [dict(item) for item in base_messages]
    current_tools = base_tools
    persona_id = "persona-a"
    settings = {"temperature": 0}
    model_route = {"requested_model": "kimi-a"}
    if changed_domain == "persona":
        persona_id = "persona-b"
    elif changed_domain == "system":
        current_messages[0] = {"role": "system", "content": "changed system"}
    elif changed_domain == "runtime":
        current_messages[1] = {
            "role": "system",
            "content": "<agent_friend_runtime_context>changed</agent_friend_runtime_context>",
        }
    elif changed_domain == "tools":
        current_tools = [{"type": "function", "function": {"name": "new_tool"}}]
    elif changed_domain == "settings":
        settings = {"temperature": 1}
    elif changed_domain == "model-route":
        model_route = {"requested_model": "kimi-b"}
    else:
        current_messages[2] = {"role": "user", "content": "rewritten history"}

    current = _begin(
        tracker,
        current_messages,
        tools=current_tools,
        persona_id=persona_id,
        settings=settings,
        model_route=model_route,
    )
    comparison = tracker.comparison_for(current, run_id="run-b")

    assert comparison.scope == "cross_user_turn"
    assert comparison.first_divergence_domain == changed_domain
    assert comparison.change_flags is not None
    assert comparison.change_flags[expected_flag] is True


def test_process_resume_uses_only_coarse_nonreversible_fingerprints() -> None:
    tracker = PromptCacheTraceTracker()
    secret = "DO_NOT_PERSIST_THIS_PROMPT"
    messages = [{"role": "user", "content": secret}]
    seed = _begin(
        tracker,
        messages,
        persona_id="persona-a",
        settings={"temperature": 0},
        model_route={"requested_model": "kimi"},
    )
    durable = tracker.finish(
        seed,
        cache_state="reported_zero",
        run_id="run-a",
        witness=_witness(messages),
        input_tokens=42,
        response_model="kimi-k2.6",
    )
    encoded = json.dumps(durable)

    assert secret not in encoded
    assert "message_digests" not in encoded
    assert "transcript_item_digests" not in encoded

    restored = PromptCacheTraceTracker()
    restored.restore(durable, finished_at_unix_ms=durable["finished_at_unix_ms"])
    current = _begin(
        restored,
        [*messages, {"role": "assistant", "content": "answer"}],
        persona_id="persona-a",
        settings={"temperature": 0},
        model_route={"requested_model": "kimi"},
    )
    comparison = restored.comparison_for(current, run_id="run-b")

    assert comparison.scope == "cross_process_resume"
    assert comparison.precision == "domain_only"
    assert comparison.previous_witness is None
    assert comparison.first_divergence_index is None
    assert comparison.first_divergence_role is None
    assert comparison.coarse_relation == "coarse_domain_change"


def test_non_user_run_does_not_displace_last_user_turn_anchor() -> None:
    tracker = PromptCacheTraceTracker()
    user_messages = [{"role": "user", "content": "user turn"}]
    user_trace = _begin(tracker, user_messages)
    user_witness = _witness(user_messages)
    tracker.finish(
        user_trace,
        cache_state="reported_zero",
        run_id="user-run-a",
        witness=user_witness,
        user_turn=True,
    )

    system_messages = [{"role": "user", "content": "system-triggered turn"}]
    system_trace = _begin(tracker, system_messages)
    tracker.finish(
        system_trace,
        cache_state="reported_zero",
        run_id="system-run",
        witness=_witness(system_messages),
        user_turn=False,
    )

    next_user = _begin(
        tracker,
        [*user_messages, {"role": "assistant", "content": "answer"}],
    )
    comparison = tracker.comparison_for(next_user, run_id="user-run-b")

    assert comparison.scope == "cross_user_turn"
    assert comparison.previous_witness == user_witness


def test_skill_snapshot_change_is_classified_in_tools_domain() -> None:
    tracker = PromptCacheTraceTracker()
    messages = [{"role": "user", "content": "use a skill"}]
    first = _begin(
        tracker,
        messages,
        skills={"selections": [{"id": "skill-a", "version": "1"}]},
    )
    tracker.finish(
        first,
        cache_state="reported_zero",
        run_id="run-a",
        witness=_witness(messages),
    )

    second = _begin(
        tracker,
        messages,
        skills={"selections": [{"id": "skill-b", "version": "1"}]},
    )
    comparison = tracker.comparison_for(second, run_id="run-b")

    assert comparison.first_divergence_domain == "tools"
    assert comparison.change_flags is not None
    assert comparison.change_flags["skills_changed"] is True
    assert comparison.change_flags["tools_changed"] is False


@pytest.mark.parametrize(
    (
        "previous_messages",
        "previous_domains",
        "current_messages",
        "current_domains",
        "expected_presence",
        "expected_content",
        "expected_position",
    ),
    [
        (
            [{"role": "system", "content": "system"}, {"role": "user", "content": "q"}],
            ("system", "messages"),
            [{"role": "system", "content": "system"}, {"role": "user", "content": "q"}],
            ("system", "messages"),
            False,
            False,
            False,
        ),
        (
            [{"role": "system", "content": "system"}, {"role": "user", "content": "q"}],
            ("system", "messages"),
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "memory"),
            True,
            False,
            False,
        ),
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "memory"),
            [{"role": "system", "content": "system"}, {"role": "user", "content": "q"}],
            ("system", "messages"),
            True,
            False,
            False,
        ),
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "memory"),
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "memory"),
            False,
            False,
            False,
        ),
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "memory"),
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-b"},
            ],
            ("system", "messages", "memory"),
            False,
            True,
            False,
        ),
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "memory"),
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a"},
                {"role": "system", "content": "memory-a"},
            ],
            ("system", "messages", "messages", "memory"),
            False,
            False,
            True,
        ),
    ],
)
def test_memory_presence_content_and_position_matrix(
    previous_messages: list[dict[str, Any]],
    previous_domains: tuple[str, ...],
    current_messages: list[dict[str, Any]],
    current_domains: tuple[str, ...],
    expected_presence: bool,
    expected_content: bool,
    expected_position: bool,
) -> None:
    tracker = PromptCacheTraceTracker()
    previous = _begin(
        tracker,
        previous_messages,
        message_domains=previous_domains,
    )
    tracker.finish(
        previous,
        cache_state="reported_zero",
        run_id="run-a",
        witness=_witness(previous_messages),
    )
    current = _begin(
        tracker,
        current_messages,
        message_domains=current_domains,
    )
    comparison = tracker.comparison_for(current, run_id="run-b")

    assert comparison.change_flags is not None
    assert comparison.change_flags["memory_presence_changed"] is expected_presence
    assert comparison.change_flags["memory_content_changed"] is expected_content
    assert comparison.change_flags["memory_position_changed"] is expected_position
    expected_domain = (
        "memory" if expected_presence or expected_content or expected_position else None
    )
    assert comparison.first_divergence_domain == expected_domain


def test_cross_process_memory_keeps_only_coarse_presence_and_position() -> None:
    tracker = PromptCacheTraceTracker()
    secret = "LOW_ENTROPY_PRIVATE_MEMORY"
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "q"},
        {"role": "system", "content": secret},
    ]
    trace = _begin(
        tracker,
        messages,
        message_domains=("system", "messages", "memory"),
    )
    durable = tracker.finish(
        trace,
        cache_state="reported_zero",
        run_id="run-a",
        witness=_witness(messages),
    )
    encoded = json.dumps(durable)

    assert secret not in encoded
    assert "memory_item_digests" not in encoded
    assert durable["cross_turn_lineage"]["memory_present"] is True
    assert durable["cross_turn_lineage"]["memory_item_count"] == 1

    restored = PromptCacheTraceTracker()
    restored.restore(durable, finished_at_unix_ms=durable["finished_at_unix_ms"])
    current = _begin(
        restored,
        messages,
        message_domains=("system", "messages", "memory"),
    )
    comparison = restored.comparison_for(current, run_id="run-b")
    assert comparison.change_flags is not None
    assert comparison.change_flags["memory_presence_changed"] is False
    assert comparison.change_flags["memory_content_changed"] is None
    assert comparison.change_flags["memory_position_changed"] is False
