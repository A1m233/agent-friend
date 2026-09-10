from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import replace
from typing import Any

import httpx
import openai
import pytest
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from llm_providers.litellm_transport import LiteLLMCompletion
from llm_providers.request_witness import ProviderRequestCapture, ProviderRequestWitness

from llm_providers import (
    LLMBadRequestError,
    LLMClient,
    ModelCallControl,
    ModelRequest,
    ProviderSpec,
    build_provider_spec,
)


class StopRequested(Exception):
    pass


def _spec() -> ProviderSpec:
    return build_provider_spec(
        provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture", reasoning_preset="low"
    )


def _request(cancel_check: Callable[[], None]) -> ModelRequest:
    return ModelRequest(
        messages=[{"role": "user", "content": "Summarize synthetic context."}],
        settings={"max_tokens": 4096},
        control=ModelCallControl(cancel_check=cancel_check, transcript_item_domains=("history",)),
    )


def _mock_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> list[httpx.AsyncClient]:
    sessions: list[httpx.AsyncClient] = []

    def make_client(spec: ProviderSpec, capture: ProviderRequestCapture) -> tuple[Any, Any]:
        session = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [capture.observe_async]},
        )
        sessions.append(session)
        if not spec.model.startswith("openai/"):

            class InjectedHTTPHandler(AsyncHTTPHandler):
                def create_client(self, *_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
                    return session

            owner = InjectedHTTPHandler()
            return owner, owner
        client = openai.AsyncOpenAI(
            api_key=spec.api_key, base_url=spec.api_base, max_retries=0, http_client=session
        )
        return client, client

    monkeypatch.setattr("llm_providers.litellm_transport._async_client", make_client)
    return sessions


def _response() -> dict[str, Any]:
    return {
        "id": "fixture-chat",
        "object": "chat.completion",
        "created": 0,
        "model": "kimi-k3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "fixture summary"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def test_cancellable_generate_keeps_chat_parameters_usage_and_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response())

    sessions = _mock_http(monkeypatch, handler)
    response = LLMClient(replace(_spec(), responses=None)).generate(_request(lambda: None))

    assert response.text == "fixture summary"
    assert response.usage is not None and response.usage.total_tokens == 12
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.moonshot.cn/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["model"] == "kimi-k3"
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 4096
    assert not body.get("stream", False)
    assert "allowed_openai_params" not in body
    assert response.diagnostics is not None
    assert response.diagnostics.transport == "litellm"
    assert isinstance(response.diagnostics.request_witness, ProviderRequestWitness)
    assert response.diagnostics.request_witness.transcript_item_domains == ("history",)
    assert len(sessions) == 1 and sessions[0].is_closed
    assert not any(t.name == "litellm-cancellable-completion" for t in threading.enumerate())


@pytest.mark.parametrize(
    ("provider", "model", "expected_host"),
    [
        ("deepseek", "deepseek/deepseek-v4-flash", "api.deepseek.com"),
        ("openrouter", "openrouter/openai/gpt-5.6-luna", "openrouter.ai"),
    ],
)
def test_cancellable_generate_uses_non_openai_async_handler(
    monkeypatch: pytest.MonkeyPatch, provider: str, model: str, expected_host: str
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response())

    sessions = _mock_http(monkeypatch, handler)
    spec = build_provider_spec(provider_id=provider, model_id=model, api_key="fixture")
    response = LLMClient(replace(spec, responses=None)).generate(_request(lambda: None))

    assert response.text == "fixture summary"
    assert len(requests) == 1
    assert requests[0].url.host == expected_host
    assert requests[0].url.path.endswith("/chat/completions")
    assert not json.loads(requests[0].content).get("stream", False)
    assert len(sessions) == 1 and sessions[0].is_closed
    assert response.diagnostics is not None
    assert isinstance(response.diagnostics.request_witness, ProviderRequestWitness)


@pytest.mark.parametrize("waiting_for", ["headers", "body"])
def test_generate_cancels_real_async_http_wait_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch, waiting_for: str
) -> None:
    waiting = threading.Event()
    cancelled = threading.Event()
    body_closed = threading.Event()

    async def stall() -> None:
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class WaitingBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            await stall()
            yield b"unreachable"

        async def aclose(self) -> None:
            body_closed.set()

    async def handler(_request: httpx.Request) -> httpx.Response:
        if waiting_for == "headers":
            await stall()
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=WaitingBody()
        )

    def cancel_check() -> None:
        if waiting.is_set():
            raise StopRequested("stop requested")

    sessions = _mock_http(monkeypatch, handler)
    started = time.monotonic()
    with pytest.raises(StopRequested, match="stop requested") as raised:
        LLMClient(replace(_spec(), responses=None)).generate(_request(cancel_check))

    assert time.monotonic() - started < 2.0
    assert cancelled.is_set()
    assert len(sessions) == 1 and sessions[0].is_closed
    if waiting_for == "body":
        assert body_closed.is_set()
    assert isinstance(getattr(raised.value, "request_witness", None), ProviderRequestWitness)
    assert not any(t.name == "litellm-cancellable-completion" for t in threading.enumerate())


def test_generate_already_cancelled_never_constructs_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cancel_check() -> None:
        raise StopRequested("already stopped")

    monkeypatch.setattr(
        "llm_providers.litellm_transport._async_client",
        lambda *_args: pytest.fail("cancelled generation must not create an HTTP client"),
    )
    with pytest.raises(StopRequested, match="already stopped"):
        LLMClient(replace(_spec(), responses=None)).generate(_request(cancel_check))


def test_generate_retry_backoff_is_cancellable(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = False
    calls = 0
    sleeps: list[float] = []

    def failing_call(*_args: Any, **_kwargs: Any) -> LiteLLMCompletion:
        nonlocal calls
        calls += 1
        raise ConnectionError("temporary network error")

    def sleep(seconds: float) -> None:
        nonlocal stop
        sleeps.append(seconds)
        stop = True

    def cancel_check() -> None:
        if stop:
            raise StopRequested("cancelled during retry wait")

    monkeypatch.setattr("llm_providers.client.complete_cancellable_litellm", failing_call)
    monkeypatch.setattr("llm_providers.client.time.sleep", sleep)
    client = LLMClient(replace(_spec(), responses=None), network_retry_backoff_seconds=60)
    with pytest.raises(StopRequested, match="cancelled during retry wait"):
        client.generate(_request(cancel_check))

    assert calls == 1
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.05


def test_generate_discards_response_when_cancel_arrives_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = False

    def completed_call(*_args: Any, **_kwargs: Any) -> LiteLLMCompletion:
        nonlocal stop
        stop = True
        return LiteLLMCompletion(response=object(), request_witness=None)

    def cancel_check() -> None:
        if stop:
            raise StopRequested("completed concurrently with cancellation")

    monkeypatch.setattr("llm_providers.client.complete_cancellable_litellm", completed_call)
    with pytest.raises(StopRequested, match="completed concurrently"):
        LLMClient(replace(_spec(), responses=None)).generate(_request(cancel_check))


def test_cancellable_generate_preserves_provider_failure_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            400, json={"error": {"type": "invalid_request_error", "message": "bad fixture"}}
        )

    sessions = _mock_http(monkeypatch, handler)
    with pytest.raises(LLMBadRequestError) as raised:
        LLMClient(replace(_spec(), responses=None)).generate(_request(lambda: None))

    assert len(requests) == 1
    assert len(sessions) == 1 and sessions[0].is_closed
    assert isinstance(getattr(raised.value, "request_witness", None), ProviderRequestWitness)
