"""A shortened request's old usage ceiling is not its measured input cost."""

from __future__ import annotations

from typing import Any, cast

import pytest
from agent.context.budget import (
    assess_request_budget,
    estimate_request_tokens,
    make_budget_snapshot,
)
from agent.context.protocol import RuntimeContext
from agent.context.usage_anchor import (
    InputEstimate,
    RequestMeasurement,
    UsageAnchor,
    digest,
    estimate_full_input,
    estimate_input,
)
from agent.messages import Message

from llm_providers import LLMUsage


def _anchor(messages: list[dict[str, Any]]) -> UsageAnchor:
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            messages, digest("request"), compatibility_scope=digest("route")
        ),
        LLMUsage(prompt_tokens=31_500, input_token_accounting="inclusive_total"),
    )
    assert anchor is not None
    return anchor


def _assess(messages: list[dict[str, Any]], anchor: UsageAnchor) -> InputEstimate:
    return estimate_input(
        messages,
        scope=anchor.scope,
        compatibility_scope=anchor.compatibility_scope,
        anchor=anchor,
        fallback_tokens=lambda: estimate_full_input(
            messages, None, unknown_token_reserve=40_000, anchor=anchor
        ),
        unknown_token_reserve=40_000,
        fallback_threshold=32_000,
    )


@pytest.mark.parametrize("ciphertext_size", [1, 100_000])
def test_replacing_complete_opaque_group_releases_old_ceiling(ciphertext_size: int) -> None:
    opaque = {
        "role": "assistant",
        "reasoning_details": [{"encrypted_content": "x" * ciphertext_size}],
    }
    history: list[dict[str, Any]] = [{"role": "user", "content": "old details " * 10_000}, opaque]
    anchor = _anchor(history)
    unchanged = _assess(history, anchor)
    assert unchanged.tokens == 31_500
    assert unchanged.input_cost_known
    assert not unchanged.is_upper_bound

    # One retained opaque group keeps a whole-input ceiling, regardless of
    # ciphertext length. Its excess is insufficient evidence of actual excess.
    new_result = {"role": "tool", "content": "a" * 1000, "tool_call_id": "settled"}
    shortened = _assess([opaque, new_result], anchor)
    assert shortened.tokens == 32_250
    assert shortened.is_upper_bound
    assert shortened.unknown_components == 0
    assert shortened.notes()["input_cost_known"] is False
    assert shortened.notes()["input_is_upper_bound"] is True

    # A checkpoint replaces the completed group as a unit; this is not a
    # mutation that strips reasoning from a retained native assistant message.
    checkpoint = [{"role": "system", "content": "completed work and remaining task"}]
    replacement = _assess(checkpoint, anchor)
    assert replacement.tokens < 100
    assert replacement.reason == "rewritten_candidate"
    assert replacement.input_cost_known
    assert not replacement.is_upper_bound


def test_repeated_opaque_occurrence_cannot_reuse_one_measured_copy_for_free() -> None:
    opaque = {"role": "assistant", "reasoning_details": [{"encrypted_content": "opaque"}]}
    history: list[dict[str, Any]] = [{"role": "user", "content": "old"}, opaque]
    result = _assess([opaque, opaque], _anchor(history))
    assert result.tokens == 31_500 + 40_000
    assert result.unknown_components == 1
    assert result.unknown_tokens == 40_000
    assert result.is_upper_bound
    assert not result.input_cost_known


def test_incompatible_opaque_evidence_is_unknown_instead_of_borrowing_old_ceiling() -> None:
    opaque = {"role": "assistant", "reasoning_details": [{"encrypted_content": "opaque"}]}
    anchor = _anchor([opaque])
    result = estimate_input(
        [opaque],
        scope=digest("new request"),
        compatibility_scope=digest("new route"),
        anchor=anchor,
        fallback_tokens=estimate_full_input([opaque], None, unknown_token_reserve=40_000),
        unknown_token_reserve=40_000,
    )
    assert result.tokens == 40_000
    assert result.unknown_components == 1
    assert result.anchor_tokens == 0
    assert not result.is_upper_bound
    assert not result.input_cost_known


def test_native_document_ceiling_and_unknown_document_remain_distinct() -> None:
    document = {"role": "user", "content": [{"type": "input_file", "file_id": "original"}]}
    anchor = _anchor([{"role": "user", "content": "old"}, document])
    retained = _assess([document], anchor)
    assert retained.tokens == 31_500
    assert retained.is_upper_bound
    assert retained.unknown_components == 0
    added = _assess(
        [{"role": "user", "content": [{"type": "input_file", "file_id": "new"}]}], anchor
    )
    assert added.unknown_tokens == 40_000
    assert added.reason == "unmeasured_document_input"
    assert not added.input_cost_known


def test_structured_budget_callback_is_shared_by_assessment_and_numeric_consumers() -> None:
    expected = InputEstimate(31_500, "usage_anchor_partial", "none", is_upper_bound=True)
    messages = [Message(role="user", content="current task")]

    def assess(candidate: list[Message]) -> InputEstimate:
        assert candidate is messages
        return expected

    def obsolete(candidate: list[Message]) -> int:
        pytest.fail("structured assessment must own the numeric result too")

    runtime = RuntimeContext(
        budget=make_budget_snapshot(40_000, None),
        llm_client=cast(Any, object()),
        request_assessment=assess,
        request_estimator=obsolete,
    )
    assert assess_request_budget(messages, runtime) is expected
    assert estimate_request_tokens(messages, runtime) == expected.tokens


def test_budget_assessment_preserves_legacy_estimator_and_calibrated_fallback() -> None:
    messages = [Message(role="user", content="a" * 100)]
    runtime = RuntimeContext(
        budget=make_budget_snapshot(40_000, None),
        llm_client=cast(Any, object()),
        request_overhead_tokens=5,
        calibration_factor=1.5,
    )
    fallback = assess_request_budget(messages, runtime)
    assert fallback.tokens == 120
    assert fallback.input_cost_known and not fallback.is_upper_bound
    runtime.request_estimator = lambda candidate: 27
    legacy = assess_request_budget(messages, runtime)
    assert legacy.tokens == 27
    assert legacy.reason == "legacy_request_estimator"
