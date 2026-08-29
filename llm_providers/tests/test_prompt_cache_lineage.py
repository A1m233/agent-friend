from __future__ import annotations

import json

from llm_providers.request_witness import witness_from_body

from llm_providers import (
    LLMResponseDiagnostics,
    LLMUsage,
    ModelResponse,
    OperationRequestLineageTracker,
)


def _response(label: str, *, input_tokens: int = 100) -> ModelResponse:
    witness = witness_from_body(
        json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "stable"},
                    {"role": "user", "content": label},
                ]
            }
        ).encode(),
        provider_id="test",
        transport="litellm",
        transform_identity="test:v1",
    )
    return ModelResponse(
        text="ok",
        usage=LLMUsage(prompt_tokens=input_tokens),
        diagnostics=LLMResponseDiagnostics(
            response_model="test-model",
            request_witness=witness,
        ),
    )


def test_operation_lineage_becomes_exact_after_first_success() -> None:
    tracker = OperationRequestLineageTracker()
    first = tracker.begin(
        session_id="session-a",
        operation="memory_extraction",
        call_kind="base_extraction",
        prompt_contract_revision="1",
    )

    assert first.scope == "unavailable"
    assert first.unavailable_reason == "no_previous_operation_request"
    assert tracker.complete(first, _response("first")) is True

    second = tracker.begin(
        session_id="session-a",
        operation="memory_extraction",
        call_kind="base_extraction",
        prompt_contract_revision="1",
    )

    assert second.scope == "within_operation"
    assert second.precision == "exact_item"
    assert second.previous_witness is not None
    assert second.previous_item_count == 2
    assert second.previous_input_tokens == 100
    assert second.previous_response_model == "test-model"


def test_operation_contracts_and_sessions_do_not_share_anchors() -> None:
    tracker = OperationRequestLineageTracker()
    base = tracker.begin(
        session_id="session-a",
        operation="memory_extraction",
        call_kind="base_extraction",
        prompt_contract_revision="1",
    )
    tracker.complete(base, _response("base"))

    reconciliation = tracker.begin(
        session_id="session-a",
        operation="memory_reconciliation",
        call_kind="semantic_reconciliation",
        prompt_contract_revision="1",
    )
    other_session = tracker.begin(
        session_id="session-b",
        operation="memory_extraction",
        call_kind="base_extraction",
        prompt_contract_revision="1",
    )

    assert reconciliation.scope == "unavailable"
    assert other_session.scope == "unavailable"
    assert reconciliation.previous_witness is None
    assert other_session.previous_witness is None


def test_older_concurrent_completion_cannot_overwrite_newer_anchor() -> None:
    tracker = OperationRequestLineageTracker()
    older = tracker.begin(
        session_id="session-a",
        operation="context_compaction",
        call_kind="summary_generation",
        prompt_contract_revision="1",
    )
    newer = tracker.begin(
        session_id="session-a",
        operation="context_compaction",
        call_kind="summary_generation",
        prompt_contract_revision="1",
    )

    assert tracker.complete(older, _response("older")) is False
    expected = _response("newer", input_tokens=200)
    assert tracker.complete(newer, expected) is True

    following = tracker.begin(
        session_id="session-a",
        operation="context_compaction",
        call_kind="summary_generation",
        prompt_contract_revision="1",
    )
    assert following.previous_input_tokens == 200
    assert expected.diagnostics is not None
    assert following.previous_witness == expected.diagnostics.request_witness


def test_missing_session_identity_is_explicitly_unavailable() -> None:
    binding = OperationRequestLineageTracker().begin(
        session_id=None,
        operation="context_compaction",
        call_kind="summary_generation",
        prompt_contract_revision="1",
    )

    assert binding.scope == "unavailable"
    assert binding.unavailable_reason == "missing_session_identity"


def test_fresh_process_tracker_does_not_claim_a_restored_operation_anchor() -> None:
    original = OperationRequestLineageTracker()
    first = original.begin(
        session_id="session-a",
        operation="memory_extraction",
        call_kind="base_extraction",
        prompt_contract_revision="1",
    )
    assert original.complete(first, _response("first")) is True

    restarted = OperationRequestLineageTracker()
    resumed = restarted.begin(
        session_id="session-a",
        operation="memory_extraction",
        call_kind="base_extraction",
        prompt_contract_revision="1",
    )

    assert resumed.scope == "unavailable"
    assert resumed.precision == "unavailable"
    assert resumed.previous_witness is None
    assert resumed.unavailable_reason == "no_previous_operation_request"
