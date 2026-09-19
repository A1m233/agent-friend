"""Protocol fixtures contain no real conversation or provider data."""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict
from typing import Any, cast, get_args

import pytest
import test_responses as fixtures
from llm_providers.errors import LLMIncompleteResponseError, LLMProviderError
from llm_providers.responses.evidence import REASONING_EVENTS, REFUSAL_EVENTS, UNSUPPORTED_EVENTS
from llm_providers.responses.stream import ResponsesState
from llm_providers.stream_events import LLMResponseDiagnostics
from openai.types.responses import ResponseStreamEvent
from test_responses import added, delta, message, terminal, text_events

from llm_providers import LLMClient, LLMTextDelta, LLMTurnDone, ModelRequest, build_provider_spec


@pytest.fixture
def http_mock(monkeypatch: pytest.MonkeyPatch) -> Any:
    return cast(Any, fixtures.http_mock).__wrapped__(monkeypatch)


def state() -> ResponsesState:
    return ResponsesState(build_provider_spec(provider_id="deepseek", api_key="fixture"))


def test_locked_sdk_event_inventory_is_explicitly_classified() -> None:
    members = get_args(get_args(ResponseStreamEvent)[0])
    sdk_events = {
        name for member in members for name in get_args(member.model_fields["type"].annotation)
    }
    supported = {
        "response.created",
        "response.in_progress",
        "response.queued",
        "response.completed",
        "response.failed",
        "response.incomplete",
        "error",
        "response.output_item.added",
        "response.output_item.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.output_text.delta",
        "response.output_text.done",
        "response.output_text.annotation.added",
    }
    assert sdk_events <= supported | REASONING_EVENTS | REFUSAL_EVENTS | UNSUPPORTED_EVENTS


@pytest.mark.parametrize("kind", sorted(UNSUPPORTED_EVENTS))
def test_known_unimplemented_capabilities_are_not_silently_ignored(kind: str) -> None:
    with pytest.raises(LLMProviderError) as caught:
        state().feed({"type": kind})
    assert caught.value.error_evidence is not None
    assert caught.value.error_evidence.code == "unsupported_response_capability"


@pytest.mark.parametrize("kind", sorted(REASONING_EVENTS))
def test_reasoning_events_validate_item_and_preserve_private_state(kind: str) -> None:
    parser = ResponsesState(
        build_provider_spec(provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture")
    )
    item = {"type": "reasoning", "id": "r1", "summary": []}
    parser.feed(added(item, 0))
    parser.feed({"type": kind, "item_id": "r1", "output_index": 0, "delta": "PRIVATE_REASONING"})
    parser.feed(terminal([item, message("m1", "hello")]))
    done = parser.finish(LLMResponseDiagnostics(transport="responses"))
    assert done.assistant_turn is not None
    assert done.assistant_turn.content == "hello"
    assert done.reasoning_state is not None


def metadata(kind: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "item_id": "m1", "output_index": 0, "content_index": 0, **extra}


@pytest.mark.parametrize("null_annotation", [False, True])
def test_queued_annotations_and_completion_metadata_through_sdk(
    http_mock: Any, null_annotation: bool
) -> None:
    events = text_events()
    events.insert(0, {"type": "response.queued", "response": {"id": "resp-1", "status": "queued"}})
    events.insert(
        2, metadata("response.content_part.added", part={"type": "output_text", "text": ""})
    )
    events[5:5] = [
        metadata(
            "response.output_text.annotation.added",
            annotation_index=0,
            annotation={
                "type": "file_citation",
                "file_id": "file-1",
                "index": 0,
                "filename": "private.pdf",
            },
        ),
        metadata("response.output_text.done", text="hello"),
        metadata("response.content_part.done", part={"type": "output_text", "text": "hello"}),
    ]
    if null_annotation:
        events[5]["annotation"] = None
    requests = http_mock(events)
    result = list(
        LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
            ModelRequest(messages=[])
        )
    )
    assert len(requests) == 1
    assert "".join(e.text for e in result if isinstance(e, LLMTextDelta)) == "hello"
    assert sum(isinstance(e, LLMTurnDone) for e in result) == 1
    done = result[-1]
    assert isinstance(done, LLMTurnDone) and done.assistant_turn is not None
    assert done.assistant_turn.text_items[0].phase == "final_answer"


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"type": None},
        {"type": []},
        {"type": 123},
        {"type": "response.reasoning_unknown.delta"},
        {"type": "response.refusal_unknown.done"},
        {"type": "response.unrecognized"},
        {"type": "response.output_item.added", "output_index": 0, "item": {"id": "i", "type": []}},
        {"type": "response.output_item.done", "output_index": 0, "item": {"id": []}},
    ],
)
def test_malformed_events_are_protocol_errors_with_evidence(event: dict[str, Any]) -> None:
    with pytest.raises(LLMProviderError) as caught:
        state().feed(event)
    evidence = caught.value.error_evidence
    assert evidence and evidence.protocol and evidence.code
    assert evidence.protocol.event_number == 1
    assert evidence.protocol.stage == "feed"


@pytest.mark.parametrize(
    "event",
    [
        metadata("response.output_text.annotation.added", annotation_index=-1, annotation={}),
        metadata("response.output_text.annotation.added", annotation_index=0, annotation="private"),
        metadata("response.output_text.done", text="different"),
        metadata("response.content_part.done", part={"type": "audio"}),
        metadata("response.output_text.done", text="hello", content_index=True),
        metadata("response.output_text.done", text="hello", item_id=[]),
    ],
)
def test_metadata_cannot_bypass_item_and_content_validation(event: dict[str, Any]) -> None:
    parser = state()
    parser.feed(added(message("m1", "hello"), 0))
    parser.feed(delta("m1", 0, "hello"))
    with pytest.raises(LLMProviderError):
        parser.feed(event)


def test_text_done_snapshot_cannot_disagree_with_terminal() -> None:
    parser = state()
    parser.feed(added(message("m1", "hello"), 0))
    parser.feed(metadata("response.output_text.done", text="hello"))
    with pytest.raises(LLMProviderError, match="conflicts with metadata"):
        parser.feed(terminal([message("m1", "different")]))


def test_annotation_cannot_reference_content_missing_from_final_snapshot() -> None:
    parser = state()
    parser.feed(added(message("m1", "hello"), 0))
    parser.feed(
        metadata(
            "response.output_text.annotation.added",
            content_index=99,
            annotation_index=0,
            annotation=None,
        )
    )
    with pytest.raises(LLMProviderError, match="content missing"):
        parser.feed(terminal([message("m1", "hello")]))


def test_text_after_done_and_response_after_terminal_are_rejected() -> None:
    parser = state()
    parser.feed(added(message("m1", "hello"), 0))
    parser.feed(metadata("response.output_text.done", text="hello"))
    with pytest.raises(LLMProviderError, match="after content completion"):
        parser.feed(delta("m1", 0, "hello"))
    parser.feed(terminal([message("m1", "hello")]))
    with pytest.raises(LLMProviderError, match="after terminal"):
        parser.feed(terminal([message("m1", "hello")]))


@pytest.mark.parametrize(
    "kind", ["response.unknown", "sk-secret", "response." + "x" * 200, "private body\nsecret"]
)
def test_evidence_is_bounded_and_does_not_serialize_payload_or_unknown_keys(kind: str) -> None:
    event = {
        "type": kind,
        "delta": "PRIVATE_BODY",
        "SECRET_FIELD_NAME": "sk-private",
        "item": {"secret": "PRIVATE_REASONING"},
    }
    with pytest.raises(LLMProviderError) as caught:
        state().feed(event)
    evidence = caught.value.error_evidence
    assert evidence and evidence.protocol
    encoded = json.dumps(asdict(evidence))
    assert len(encoded) < 1500
    assert not any(
        secret in encoded
        for secret in [
            "PRIVATE_BODY",
            "SECRET_FIELD_NAME",
            "sk-private",
            "PRIVATE_REASONING",
            "sk-secret",
            "private body",
        ]
    )
    assert evidence.protocol.unknown_field_count == 1
    assert evidence.protocol.event_type_state == (
        "recorded" if kind == "response.unknown" else "redacted"
    )


def test_eof_retains_incomplete_semantics_and_finish_evidence() -> None:
    parser = state()
    parser.feed(added(message("m1", "hello"), 0))
    with pytest.raises(LLMIncompleteResponseError) as caught:
        parser.finish(LLMResponseDiagnostics(transport="responses"))
    evidence = caught.value.error_evidence
    assert evidence and evidence.protocol
    assert evidence.category == "incomplete"
    assert evidence.protocol.stage == "finish"
    assert evidence.protocol.item_count == 1


def test_malformed_terminal_error_has_finish_evidence() -> None:
    parser = state()
    parser.feed(terminal([], "failed", error="PRIVATE_ERROR"))
    with pytest.raises(LLMProviderError) as caught:
        parser.finish(LLMResponseDiagnostics(transport="responses"))
    assert caught.value.error_evidence is not None
    assert caught.value.error_evidence.protocol is not None
    assert caught.value.error_evidence.protocol.stage == "finish"
    assert "PRIVATE_ERROR" not in json.dumps(asdict(caught.value.error_evidence))


@pytest.mark.parametrize("streaming", [True, False])
def test_protocol_evidence_reaches_call_log_without_retry(
    http_mock: Any, caplog: pytest.LogCaptureFixture, streaming: bool
) -> None:
    caplog.set_level("INFO", logger="llm_providers.call_observation")
    requests = http_mock(
        [terminal([{"id": "private-id", "type": "not_supported", "text": "PRIVATE_BODY"}])]
    )
    client = LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture"))
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(LLMProviderError):
            if streaming:
                list(client.stream(ModelRequest(messages=[])))
            else:
                client.generate(ModelRequest(messages=[]))
    assert not any("PRIVATE_BODY" in str(w.message) for w in recorded)
    records = [
        json.loads(r.message.split("model_call_observation ", 1)[1])
        for r in caplog.records
        if "model_call_observation " in r.message
    ]
    assert len(requests) == len(records) == 1
    record = records[0]
    assert record["retry_plans"] == 0 and record["stop_reason"] == "not_retryable"
    assert record["error"]["protocol"]["event_type"] == "response.completed"
    assert record["error"]["request_ref"] and record["error"]["response_ref"]
    assert "PRIVATE_BODY" not in caplog.text and "private-id" not in caplog.text
