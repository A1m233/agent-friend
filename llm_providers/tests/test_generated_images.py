from __future__ import annotations

import base64
from typing import Any, cast

import pytest
import test_responses as fixtures
from llm_providers.errors import LLMProviderError, LLMStreamInterruptedAfterOutputError
from llm_providers.image_output import MAX_OUTPUT_IMAGE_BYTES, LLMImageResult
from llm_providers.responses.stream import ResponsesState
from llm_providers.stream_events import LLMResponseDiagnostics, LLMTurnDone
from test_responses import added, message, terminal

from llm_providers import LLMClient, ModelCallControl, ModelRequest, build_provider_spec


def image(
    index: int = 0, result: object = "cGljdHVyZQ==", status: str = "completed"
) -> dict[str, Any]:
    return {
        "id": f"img-{index}",
        "type": "image_generation_call",
        "status": status,
        "result": result,
    }


def parser() -> ResponsesState:
    return ResponsesState(build_provider_spec(provider_id="deepseek", api_key="fixture"))


@pytest.fixture
def http_mock(monkeypatch: pytest.MonkeyPatch) -> Any:
    return cast(Any, fixtures.http_mock).__wrapped__(monkeypatch)


def test_sdk_transport_accepts_pure_image_once(http_mock: Any) -> None:
    raw = image()
    requests = http_mock(
        [
            added(raw, 0),
            {"type": "response.output_item.done", "output_index": 0, "item": raw},
            terminal([raw]),
        ]
    )
    events = list(
        LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
            ModelRequest(messages=[])
        )
    )
    images = [event for event in events if isinstance(event, LLMImageResult)]
    assert len(images) == 1 and images[0].data == b"picture"
    assert len(requests) == 1
    done = events[-1]
    assert (
        isinstance(done, LLMTurnDone)
        and done.assistant_turn
        and done.assistant_turn.image_count == 1
    )
    assert "picture" not in repr(images[0])


def test_order_duplicates_conflicts_and_limits() -> None:
    p = parser()
    raw = image()
    p.feed({"type": "response.output_item.done", "output_index": 0, "item": raw})
    assert p.feed({"type": "response.output_item.done", "output_index": 0, "item": raw}) == []
    with pytest.raises(LLMProviderError, match="conflicting image completion"):
        p.feed(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": image(result="b3RoZXI="),
            }
        )
    p = parser()
    events = p.feed(terminal([image(i) for i in range(5)] + [message("text", "caption")]))
    images = [ev for ev in events if isinstance(ev, LLMImageResult)]
    assert [ev.output_index for ev in images] == list(range(5))
    assert images[-1].error_code == "image_count_exceeded"
    turn = p.finish(LLMResponseDiagnostics()).assistant_turn
    assert turn is not None and turn.content == "caption"


@pytest.mark.parametrize(
    "result,status,code",
    [
        (None, "completed", "image_result_missing"),
        ("?private", "completed", "image_invalid"),
        ("cGljdHVyZQ==", "failed", "image_generation_failed"),
        (
            base64.b64encode(b"x" * (MAX_OUTPUT_IMAGE_BYTES + 1)).decode(),
            "completed",
            "image_size_exceeded",
        ),
    ],
    ids=["missing", "invalid", "failed", "oversize"],
)
def test_invalid_picture_is_a_visible_failure_not_empty_response(
    result: object, status: str, code: str
) -> None:
    p = parser()
    events = p.feed(terminal([image(result=result, status=status)]))
    got = next(e for e in events if isinstance(e, LLMImageResult))
    assert got.error_code == code and not got.data
    assert p.finish(LLMResponseDiagnostics()).stop_reason == "end_turn"


def test_preview_never_becomes_a_final_picture() -> None:
    p = parser()
    p.feed(added(image(status="in_progress"), 0))
    preview = p.feed(
        {
            "type": "response.image_generation_call.partial_image",
            "item_id": "img-0",
            "output_index": 0,
            "partial_image_b64": "private",
        }
    )
    assert not any(isinstance(e, LLMImageResult) for e in preview)


def test_completion_signal_without_item_is_explicit_protocol_failure() -> None:
    with pytest.raises(LLMProviderError, match="unknown or mismatched item"):
        parser().feed(
            {
                "type": "response.image_generation_call.completed",
                "item_id": "image",
                "output_index": 0,
            }
        )


def test_provider_failure_after_image_start_never_restarts_generation(http_mock: Any) -> None:
    requests = http_mock(
        [
            added(image(status="in_progress"), 0),
            {"type": "error", "code": "server_is_overloaded", "message": "busy"},
        ]
    )
    client = LLMClient(
        build_provider_spec(provider_id="deepseek", api_key="fixture"),
        engine_overload_retry_backoff_seconds=0,
    )
    with pytest.raises(LLMStreamInterruptedAfterOutputError):
        list(
            client.stream(
                ModelRequest(messages=[], control=ModelCallControl(allow_output_retries=True))
            )
        )
    assert len(requests) == 1
