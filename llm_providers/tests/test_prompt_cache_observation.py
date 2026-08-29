from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from llm_providers import (
    PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
    LLMClient,
    LLMResponseDiagnostics,
    LLMUsage,
    ModelCallControl,
    ModelCallTraceContext,
    ModelRequest,
    ProviderSpec,
    build_prompt_cache_observation,
    classify_prompt_cache_result,
)


@pytest.mark.parametrize(
    ("relation", "cache_read", "expected"),
    [
        ("baseline", None, "baseline_request"),
        ("same_request_retry", 0, "transport_retry_result"),
        ("strict_extension", 20, "structure_preserved_cache_read"),
        ("strict_extension", 0, "structure_preserved_no_cache_read"),
        ("strict_extension", None, "structure_preserved_usage_unavailable"),
        ("rebase", 20, "structure_changed"),
        ("unavailable", 20, "cache_read_structure_unavailable"),
        ("unavailable", 0, "no_cache_read_structure_unavailable"),
        ("unavailable", None, "insufficient_observation"),
    ],
)
def test_classify_prompt_cache_result_matrix(
    relation: str,
    cache_read: int | None,
    expected: str,
) -> None:
    assert classify_prompt_cache_result(relation, cache_read) == expected


def test_observation_is_content_free_and_reconciles_usage() -> None:
    secret = "raw-secret-session-and-prompt"
    usage = LLMUsage(
        prompt_tokens=200,
        completion_tokens=8,
        total_tokens=208,
        cached_prompt_tokens=100,
        cache_write_prompt_tokens=25,
        input_other_prompt_tokens=75,
        input_token_accounting="inclusive_total",
    )
    observation = build_prompt_cache_observation(
        requested_model="deepseek/deepseek-chat",
        provider_id="deepseek",
        message_count=3,
        tool_count=2,
        usage=usage,
        diagnostics=LLMResponseDiagnostics(
            transport="litellm",
            request_relation="strict_extension",
            common_prefix_items=2,
            provider_request_id=secret,
            response_id=secret,
            response_model="deepseek-chat",
        ),
        control=ModelCallControl(
            trace_context=ModelCallTraceContext(
                operation="conversation",
                surface="desktop_chat",
                run_id=secret,
                session_id=secret,
                call_kind="tool_continuation",
                call_index=2,
                adapter_trace={
                    "prompt_fingerprint": "safe-digest",
                    "prompt_payload_bytes": 1234,
                    "raw_prompt": secret,
                },
            )
        ),
        total_elapsed_ms=50,
    )

    encoded = json.dumps(observation, ensure_ascii=False)
    assert secret not in encoded
    assert "raw_prompt" not in encoded
    assert len(observation["identity"]["run_ref"]) == 16
    assert len(observation["identity"]["session_ref"]) == 16
    assert observation["identity"]["run_ref"] != observation["identity"]["session_ref"]
    assert observation["usage"]["buckets_reconciled"] is True
    assert observation["identity"]["code"]["collector"] == "llm-providers"
    assert (
        observation["identity"]["code"]["collector_revision"]
        == PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION
    )
    assert observation["outcome"]["interpretation"] == "structure_preserved_cache_read"
    assert observation["structure"]["adapter"] == {
        "prompt_fingerprint": "safe-digest",
        "prompt_payload_bytes": 1234,
    }


def test_observation_keeps_missing_usage_distinct_from_zero() -> None:
    missing = build_prompt_cache_observation(
        requested_model="model",
        provider_id=None,
        message_count=1,
        tool_count=0,
        usage=None,
        diagnostics=None,
    )
    zero = build_prompt_cache_observation(
        requested_model="model",
        provider_id=None,
        message_count=1,
        tool_count=0,
        usage=LLMUsage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cached_prompt_tokens=0,
            cache_write_prompt_tokens=0,
            input_other_prompt_tokens=0,
            input_token_accounting="inclusive_total",
        ),
        diagnostics=None,
    )

    assert missing["usage"]["input_total"] is None
    assert missing["usage"]["cache_read"] is None
    assert zero["usage"]["input_total"] == 0
    assert zero["usage"]["cache_read"] == 0


def test_cross_user_turn_observation_reports_exact_rebase_and_lost_prefix() -> None:
    observation = build_prompt_cache_observation(
        requested_model="requested-model",
        provider_id="provider",
        message_count=4,
        tool_count=1,
        usage=LLMUsage(prompt_tokens=160, completion_tokens=5, total_tokens=165),
        diagnostics=LLMResponseDiagnostics(
            request_relation="rebase",
            common_prefix_items=3,
            previous_request_items=5,
            first_divergence_index=3,
            first_divergence_role="user",
            first_divergence_domain="messages",
            lost_prefix_items=2,
            response_model="actual-model",
        ),
        control=ModelCallControl(
            trace_context=ModelCallTraceContext(
                comparison_scope="cross_user_turn",
                comparison_precision="exact_item",
                comparison={
                    "previous_item_count": 5,
                    "previous_input_tokens": 100,
                    "previous_response_model": "actual-model",
                    "first_divergence_domain": "settings",
                    "change_flags": {
                        "settings_changed": True,
                        "messages_appended": True,
                        "messages_rewritten": False,
                    },
                },
            )
        ),
    )

    structure = observation["structure"]
    assert structure["comparison_scope"] == "cross_user_turn"
    assert structure["comparison_precision"] == "exact_item"
    assert structure["cross_turn_relation"] == "rebase"
    assert structure["first_divergence_domain"] == "settings"
    assert structure["first_divergence_index"] == 3
    assert structure["first_divergence_role"] == "user"
    assert structure["lost_prefix_items"] == 5
    assert structure["lost_prefix_tokens_estimate"] == 100
    assert structure["change_flags"]["settings_changed"] is True


def test_cross_process_resume_never_claims_wire_baseline_or_exact_prefix() -> None:
    observation = build_prompt_cache_observation(
        requested_model="model",
        provider_id="provider",
        message_count=2,
        tool_count=0,
        usage=None,
        diagnostics=LLMResponseDiagnostics(request_relation="baseline"),
        control=ModelCallControl(
            trace_context=ModelCallTraceContext(
                comparison_scope="cross_process_resume",
                comparison_precision="domain_only",
                comparison={
                    "previous_item_count": 2,
                    "previous_input_tokens": 50,
                    "coarse_relation": "coarse_domain_change",
                    "first_divergence_domain": "runtime",
                    "change_flags": {"runtime_changed": True},
                },
            )
        ),
    )

    structure = observation["structure"]
    assert structure["final_wire_relation"] == "unavailable"
    assert structure["raw_final_wire_relation"] == "baseline"
    assert structure["cross_turn_relation"] == "coarse_domain_change"
    assert structure["comparison_precision"] == "domain_only"
    assert structure["first_divergence_domain"] == "runtime"
    assert structure["common_prefix_items"] is None
    assert structure["lost_prefix_items"] is None
    assert structure["lost_prefix_tokens_estimate"] is None
    assert observation["outcome"]["interpretation"] == "insufficient_observation"


def test_non_stream_client_attaches_same_observation_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        id="response-secret-id",
        model="deepseek-chat",
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(
            prompt_tokens=20,
            completion_tokens=2,
            total_tokens=22,
            prompt_cache_hit_tokens=10,
            prompt_cache_miss_tokens=10,
        ),
    )
    monkeypatch.setattr("llm_providers.client.litellm.completion", lambda **_kwargs: response)
    client = LLMClient(
        ProviderSpec(model="deepseek/deepseek-chat", api_key="test"),
        network_retries=0,
    )

    result = client.generate(
        ModelRequest(
            messages=[{"role": "user", "content": "private prompt"}],
            control=ModelCallControl(
                trace_context=ModelCallTraceContext(
                    operation="memory_extraction",
                    session_id="private-session",
                )
            ),
        )
    )

    assert result.diagnostics is not None
    observation = result.diagnostics.cache_observation
    assert observation is not None
    assert observation["identity"]["operation"] == "memory_extraction"
    assert observation["usage"]["input_total"] == 20
    assert observation["usage"]["cache_read"] == 10
    assert observation["structure"]["final_wire_relation"] == "unavailable"
    encoded = json.dumps(observation)
    assert "private prompt" not in encoded
    assert "private-session" not in encoded
    assert "response-secret-id" not in encoded


def test_within_operation_reports_lost_prefix_and_unavailable_reason() -> None:
    observation = build_prompt_cache_observation(
        requested_model="test-model",
        provider_id="test",
        message_count=2,
        tool_count=0,
        usage=LLMUsage(prompt_tokens=220),
        diagnostics=LLMResponseDiagnostics(
            request_relation="rebase",
            common_prefix_items=1,
            previous_request_items=2,
            first_divergence_index=1,
            first_divergence_role="user",
            first_divergence_domain="messages",
            lost_prefix_items=1,
        ),
        control=ModelCallControl(
            trace_context=ModelCallTraceContext(
                operation="memory_extraction",
                comparison_scope="within_operation",
                comparison_precision="exact_item",
                comparison={
                    "previous_item_count": 2,
                    "previous_input_tokens": 200,
                    "unavailable_reason": None,
                },
            )
        ),
    )

    assert observation["structure"]["cross_turn_relation"] == "rebase"
    assert observation["structure"]["lost_prefix_items"] == 1
    assert observation["structure"]["lost_prefix_tokens_estimate"] == 100
    assert observation["structure"]["unavailable_reason"] is None

    unavailable = build_prompt_cache_observation(
        requested_model="test-model",
        provider_id="test",
        message_count=2,
        tool_count=0,
        usage=None,
        diagnostics=LLMResponseDiagnostics(request_relation="baseline"),
        control=ModelCallControl(
            trace_context=ModelCallTraceContext(
                operation="context_compaction",
                comparison_scope="unavailable",
                comparison_precision="unavailable",
                comparison={
                    "unavailable_reason": "no_previous_operation_request",
                },
            )
        ),
    )
    assert unavailable["structure"]["final_wire_relation"] == "unavailable"
    assert unavailable["structure"]["unavailable_reason"] == "no_previous_operation_request"


def test_observation_failure_does_not_break_model_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    )
    monkeypatch.setattr("llm_providers.client.litellm.completion", lambda **_kwargs: response)
    monkeypatch.setattr(
        "llm_providers.client.build_prompt_cache_observation",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("observation failed")),
    )

    result = LLMClient(
        ProviderSpec(model="deepseek/deepseek-chat", api_key="test"),
        network_retries=0,
    ).generate(ModelRequest(messages=[{"role": "user", "content": "still private"}]))

    assert result.text == "ok"
    assert result.diagnostics is not None
    assert result.diagnostics.cache_observation is None
