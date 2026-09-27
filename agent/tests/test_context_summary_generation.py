from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any

import httpx
import pytest
from agent.context import ContextCompactionUnavailableError, SummarizingContextManager
from agent.context.summary_generation import generate_summary
from llm_providers.errors import LLMIncompleteResponseError
from llm_providers.stream_events import ProviderAssistantItem, ProviderAssistantTurn

from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    build_provider_spec,
)

from .test_context_summarizing import _FakeLLM, _runtime, _turns


def test_summary_uses_only_validated_final_and_closes_stream() -> None:
    closed = False

    class Model(_FakeLLM):
        def generate(self, request: ModelRequest) -> ModelResponse:
            pytest.fail("summary must not wait for a non-streaming response")

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            nonlocal closed
            try:
                yield LLMTextDelta(text="private commentary")
                yield LLMTurnDone(
                    stop_reason="end_turn",
                    assistant_turn=ProviderAssistantTurn(
                        content=None,
                        text_items=(
                            ProviderAssistantItem("c", 0, "private commentary", "commentary"),
                            ProviderAssistantItem("f", 1, "complete summary", "final_answer"),
                        ),
                    ),
                )
            finally:
                closed = True

    assert generate_summary(Model(), ModelRequest(messages=[])).text == "complete summary"
    assert closed


@pytest.mark.parametrize("terminal", [False, True])
def test_partial_summary_is_never_accepted(terminal: bool) -> None:
    class Model(_FakeLLM):
        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            yield LLMTextDelta(text="partial summary")
            if terminal:
                yield LLMTurnDone(
                    stop_reason="max_tokens",
                    assistant_turn=ProviderAssistantTurn(content="partial summary"),
                )

    with pytest.raises(LLMIncompleteResponseError):
        generate_summary(Model(), ModelRequest(messages=[]))


@pytest.mark.parametrize("outcome", ["progress", "stall", "cancel"])
def test_compaction_http_progress_idle_timeout_and_cancel(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Exercise the real summary -> LLMClient -> SDK -> idle timer boundary."""
    from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled

    requests: list[dict[str, Any]] = []
    closed: list[threading.Event] = []
    token = CancellationToken()

    def sse(event: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(event)}\n\n".encode()

    class Body(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = threading.Event()
            closed.append(self.closed)

        async def __aiter__(self) -> AsyncIterator[bytes]:
            if outcome == "stall":
                await asyncio.Event().wait()
            # 0.8s total exceeds the 0.5s idle timeout. Individual provider
            # events arrive every 0.1s even before any final answer exists.
            for index in range(8):
                await asyncio.sleep(0.1)
                if outcome == "cancel" and index == 1:
                    token.cancel(CancelReason.EXPLICIT_REQUEST)
                yield sse({"type": "response.in_progress", "response": {"id": "resp-summary"}})
            yield sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp-summary",
                        "model": "fixture-model",
                        "object": "response",
                        "created_at": 1,
                        "status": "completed",
                        "output": [
                            {
                                "id": "summary",
                                "type": "message",
                                "role": "assistant",
                                "status": "completed",
                                "content": [{"type": "output_text", "text": "complete summary"}],
                            }
                        ],
                        "usage": {"input_tokens": 100, "output_tokens": 3, "total_tokens": 103},
                    },
                }
            )

        async def aclose(self) -> None:
            self.closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True
        return httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    spec = build_provider_spec(provider_id="deepseek", api_key="fixture")
    client = LLMClient(
        replace(spec, defaults={**spec.defaults, "timeout": 0.5}),
        network_retry_backoff_seconds=0,
        stream_timeout_seconds=0.5,
    )
    runtime = _runtime(client, 1600)
    runtime.cancel_check = token.raise_if_cancelled
    history = _turns(12, 100)
    original = [(message.uuid, message.content) for message in history]
    manager = SummarizingContextManager()

    if outcome == "progress":
        result = manager.build_messages(history, "system", runtime=runtime)
        assert result.new_compaction is not None
        assert "complete summary" in result.new_compaction.summary
    else:
        error = RunCancelled if outcome == "cancel" else ContextCompactionUnavailableError
        with pytest.raises(error):
            manager.build_messages(history, "system", runtime=runtime)
    # Summary transports do not multiply the compaction planner's shared retry
    # budget. This compatibility build has no planner-controlled retry cycle.
    assert len(requests) == 1
    assert all(event.wait(1) for event in closed)
    assert [(message.uuid, message.content) for message in history] == original
