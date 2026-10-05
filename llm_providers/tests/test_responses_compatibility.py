"""Synthetic 150 fixtures: SDK compatibility, useful results and refusal boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import test_responses as fixtures
from llm_providers.errors import LLMContentPolicyError, LLMIncompleteResponseError, LLMProviderError
from llm_providers.responses.stream import ResponsesState
from llm_providers.stream_events import LLMResponseDiagnostics
from test_responses import added, message, terminal, text_events

from llm_providers import (
    LLMClient,
    LLMTurnDone,
    ModelRequest,
    build_provider_spec,
    final_response_text,
)


@pytest.fixture
def http_mock(monkeypatch: pytest.MonkeyPatch) -> Any:
    return cast(Any, fixtures.http_mock).__wrapped__(monkeypatch)


def parser() -> ResponsesState:
    return ResponsesState(build_provider_spec(provider_id="deepseek", api_key="fixture"))


@pytest.mark.parametrize("event", [None, 123, {}, {"type": None}, {"type": []}, {"type": ""}])
def test_unrecognized_envelopes_do_not_abort_valid_terminal(event: Any) -> None:
    p = parser()
    assert p.feed(event) == []
    p.feed(terminal([message("m", "ok")]))
    assert final_response_text(p.finish(LLMResponseDiagnostics())) == "ok"


@pytest.mark.parametrize(
    "kind", ["keepalive", "response.new.delta", "response.metadata", "vendor.ping"]
)
@pytest.mark.parametrize("position", [0, 2, 4])
def test_extension_can_interleave_through_sdk(http_mock: Any, kind: str, position: int) -> None:
    events = text_events()
    events.insert(position, {"type": kind, "sequence_number": 3, "future_field": {"v": 1}})
    requests = http_mock(events)
    done = list(
        LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
            ModelRequest(messages=[])
        )
    )[-1]
    assert isinstance(done, LLMTurnDone) and final_response_text(done) == "hello"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "kind",
    [
        "future_tool",
        *[
            kind
            for kind in json.loads(
                (Path(__file__).parent / "fixtures/responses/events-2026-10-05.json").read_text(
                    encoding="utf-8"
                )
            )["output_items"]
            if kind not in {"message", "function_call", "reasoning"}
        ],
    ],
)
def test_other_output_does_not_become_executable_tool(kind: str) -> None:
    p = parser()
    p.feed(
        terminal([{"type": kind, "opaque": "private", "status": "failed"}, message("m", "answer")])
    )
    done = p.finish(LLMResponseDiagnostics())
    assert done.assistant_turn and not done.assistant_turn.tool_calls
    assert done.assistant_turn.content == "answer"


def test_keepalive_cannot_replace_terminal_and_logs_are_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="llm_providers.responses.stream")
    p = parser()
    for i in range(50):
        p.feed({"type": "keepalive", "sequence_number": i, "secret": "PRIVATE_PAYLOAD"})
    with pytest.raises(LLMIncompleteResponseError):
        p.finish(LLMResponseDiagnostics())
    assert p.skipped_events == 50
    assert len([r for r in caplog.records if "responses_event_skipped" in r.message]) == 8
    assert "PRIVATE_PAYLOAD" not in caplog.text


def test_refusal_is_visible_completed_content_but_not_an_internal_result(http_mock: Any) -> None:
    item = message("m1", "")
    item["content"] = [{"type": "refusal", "refusal": "Cannot do that."}]
    http_mock(
        [
            added(item, 0),
            {
                "type": "response.refusal.delta",
                "item_id": "m1",
                "output_index": 0,
                "content_index": 0,
                "delta": "Cannot ",
            },
            {
                "type": "response.refusal.done",
                "item_id": "m1",
                "output_index": 0,
                "content_index": 0,
                "refusal": "Cannot do that.",
            },
            terminal([item]),
        ]
    )
    done = list(
        LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
            ModelRequest(messages=[])
        )
    )[-1]
    assert isinstance(done, LLMTurnDone) and done.assistant_turn
    assert done.assistant_turn.content == "Cannot do that."
    assert done.assistant_turn.text_items[0].content_parts[0].kind == "refusal"
    with pytest.raises(LLMContentPolicyError):
        final_response_text(done)


def test_refusal_cannot_authorize_tool_calls() -> None:
    p = parser()
    item = message("m", "")
    item["content"] = [{"type": "refusal", "refusal": "No"}]
    p.feed(
        terminal(
            [
                item,
                {
                    "type": "function_call",
                    "id": "f",
                    "call_id": "c",
                    "name": "delete",
                    "arguments": "{}",
                },
            ]
        )
    )
    with pytest.raises(LLMProviderError, match="refusal response contains tool calls"):
        p.finish(LLMResponseDiagnostics())


def test_citations_keep_content_association_and_remove_opaque_fields() -> None:
    p = parser()
    item = message("m", "first")
    item["content"].append(
        {
            "type": "output_text",
            "text": "second",
            "annotations": [
                {
                    "type": "url_citation",
                    "url": "https://example.com",
                    "title": "Source",
                    "start_index": 0,
                    "end_index": 6,
                    "private": "secret",
                }
            ],
        }
    )
    p.feed(terminal([item]))
    done = p.finish(LLMResponseDiagnostics())
    assert done.assistant_turn
    part = done.assistant_turn.text_items[0].content_parts[1]
    assert (part.content_index, part.start, part.end) == (1, 5, 11)
    assert part.annotations[0]["url"] == "https://example.com"
    assert "secret" not in json.dumps(part.to_dict())


def test_completed_ignores_extra_fields_but_rejects_content_conflicts() -> None:
    p = parser()
    item = message("m", "hello")
    p.feed({"type": "response.output_item.done", "output_index": 0, "item": item})
    p.feed(terminal([{**item, "new_metadata": "value"}]))
    assert final_response_text(p.finish(LLMResponseDiagnostics())) == "hello"


def test_valid_terminal_stops_consumption_without_waiting_for_eof(http_mock: Any) -> None:
    # A known malformed business event after the terminal must never be parsed.
    http_mock([*text_events(), {"type": "response.output_text.delta"}])
    done = list(
        LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
            ModelRequest(messages=[])
        )
    )[-1]
    assert isinstance(done, LLMTurnDone) and final_response_text(done) == "hello"


def test_final_snapshot_can_add_citations_without_repeating_text() -> None:
    p = parser()
    item = message("m", "hello")
    p.feed({"type": "response.output_item.done", "output_index": 0, "item": item})
    annotated = message("m", "hello")
    annotated["content"][0]["annotations"] = [
        {"type": "url_citation", "url": "https://example.com"}
    ]
    assert p.feed(terminal([annotated])) == []
    done = p.finish(LLMResponseDiagnostics())
    assert done.assistant_turn
    assert (
        done.assistant_turn.text_items[0].content_parts[0].annotations[0]["url"]
        == "https://example.com"
    )


def test_other_only_response_fails_clearly() -> None:
    p = parser()
    p.feed(terminal([{"type": "future_tool", "opaque": "private"}]))
    with pytest.raises(LLMProviderError, match="unsupported output capability"):
        p.finish(LLMResponseDiagnostics())


def test_unrecognized_annotation_type_cannot_crash_completed_answer() -> None:
    p = parser()
    item = message("m", "hello")
    item["content"][0]["annotations"] = [{"type": {"future": True}}]
    p.feed(terminal([item]))
    done = p.finish(LLMResponseDiagnostics())
    assert done.assistant_turn
    assert done.assistant_turn.text_items[0].content_parts[0].annotations == (
        {"type": "unavailable"},
    )
