from __future__ import annotations

import json

from agent.run_transcript import RunContinuationProjection
from llm_providers.request_witness import ProviderRequestWitness, witness_from_body

from llm_providers import ProviderAssistantTurn, ProviderToolCall


def _wire(
    messages: list[dict[str, object]],
    *,
    transform: str = "provider:v1",
) -> ProviderRequestWitness:
    body = json.dumps(
        {"model": "model", "messages": messages, "temperature": 0},
        separators=(",", ":"),
    ).encode()
    return witness_from_body(
        body,
        provider_id="provider",
        transport="test",
        transform_identity=transform,
    )


def _assistant() -> ProviderAssistantTurn:
    return ProviderAssistantTurn(
        content=None,
        tool_calls=(
            ProviderToolCall(
                index=0,
                tool_call_id="call-1",
                tool_name="lookup",
                arguments_json='{"q":"x"}',
            ),
        ),
    )


def test_projection_builds_exact_strict_append_candidate_and_closes() -> None:
    baseline = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "work"},
        {"role": "user", "content": '<agent_friend_runtime_context sequence="0" />'},
    ]
    projection = RunContinuationProjection("run-1")
    first = projection.prepare(
        canonical_messages=baseline,
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1",),
        event_cursor=1,
    )
    first_witness = _wire(first.messages)
    projection.record_completed_turn(_assistant(), first_witness)

    tool_message = {"role": "tool", "tool_call_id": "call-1", "content": "result"}
    fresh_snapshot = {
        "role": "user",
        "content": '<agent_friend_runtime_context sequence="1" supersedes="0" />',
    }
    second = projection.prepare(
        canonical_messages=[*baseline, _assistant().to_openai_message(), tool_message],
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-request", "tool-result"),
        event_cursor=3,
        tool_messages=[tool_message],
        host_tail=[fresh_snapshot],
    )

    assert second.disposition == "continued"
    assert second.messages[: len(first.messages)] == first.messages
    assert second.messages[len(first.messages)] == _assistant().to_openai_message()
    assert second.messages[-2:] == [tool_message, fresh_snapshot]
    second_witness = _wire(second.messages)
    assert second_witness.relation_to(first_witness) == "strict_extension"
    projection.observe_request_witness(second_witness)
    assert projection.current_disposition == "continued"

    projection.close()
    assert projection.previous_messages is None
    assert projection.previous_witness is None
    assert projection.pending_assistant is None


def test_projection_rebuilds_for_history_envelope_compaction_and_wire_changes() -> None:
    baseline = [{"role": "user", "content": "work"}]

    def prepared_projection() -> RunContinuationProjection:
        projection = RunContinuationProjection("run")
        first = projection.prepare(
            canonical_messages=baseline,
            tools=[],
            settings={"temperature": 0},
            model_identity="provider/model",
            active_event_uuids=("user-1",),
            event_cursor=1,
        )
        projection.record_completed_turn(_assistant(), _wire(first.messages))
        return projection

    rewritten = prepared_projection()
    result = rewritten.prepare(
        canonical_messages=[{"role": "user", "content": "edited"}],
        tools=[],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-edited",),
        event_cursor=1,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
    )
    assert (result.disposition, result.rebase_reason) == ("rebuilt", "history_rewritten")

    envelope = prepared_projection()
    result = envelope.prepare(
        canonical_messages=baseline,
        tools=[{"type": "function", "function": {"name": "new-tool"}}],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
    )
    assert (result.disposition, result.rebase_reason) == (
        "rebuilt",
        "tool_envelope_changed",
    )

    settings = prepared_projection()
    result = settings.prepare(
        canonical_messages=baseline,
        tools=[],
        settings={"temperature": 0.5},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
    )
    assert (result.disposition, result.rebase_reason) == (
        "rebuilt",
        "request_settings_changed",
    )

    model = prepared_projection()
    result = model.prepare(
        canonical_messages=baseline,
        tools=[],
        settings={"temperature": 0},
        model_identity="other-provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
    )
    assert (result.disposition, result.rebase_reason) == (
        "rebuilt",
        "model_config_changed",
    )

    attachment = prepared_projection()
    result = attachment.prepare(
        canonical_messages=baseline,
        tools=[],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
        forced_rebase_reason="attachment_representation_changed",
    )
    assert (result.disposition, result.rebase_reason) == (
        "rebuilt",
        "attachment_representation_changed",
    )

    compacted = prepared_projection()
    result = compacted.prepare(
        canonical_messages=baseline,
        tools=[],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
        context_notes={"checkpoint_status": "candidate"},
    )
    assert (result.disposition, result.rebase_reason) == ("rebuilt", "context_compacted")

    transformed = prepared_projection()
    continued = transformed.prepare(
        canonical_messages=baseline,
        tools=[],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
    )
    transformed.observe_request_witness(_wire(continued.messages, transform="provider:v2"))
    assert transformed.current_disposition == "rebuilt"
    assert transformed.current_rebase_reason == "provider_transform_changed"

    unavailable = prepared_projection()
    continued = unavailable.prepare(
        canonical_messages=baseline,
        tools=[],
        settings={"temperature": 0},
        model_identity="provider/model",
        active_event_uuids=("user-1", "tool-result"),
        event_cursor=2,
        tool_messages=[{"role": "tool", "tool_call_id": "call-1", "content": "x"}],
    )
    unavailable.observe_request_witness(None)
    assert continued.disposition == "continued"
    assert unavailable.current_disposition == "continued"
    assert unavailable.current_rebase_reason == "wire_witness_unavailable"
