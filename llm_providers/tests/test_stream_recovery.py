"""Deterministic transport failures: no SDK, sockets or real model calls."""

from collections.abc import Iterator
from typing import Any

import pytest
from llm_providers.errors import (
    LLMAuthError,
    LLMStreamInterruptedAfterOutputError,
    LLMTransientProviderError,
)
from llm_providers.protocol import ModelRequest, ModelResponse

from llm_providers import (
    LLMClient,
    LLMNetworkError,
    LLMRetryScheduled,
    LLMTextDelta,
    LLMTurnDone,
    ProviderSpec,
)


@pytest.mark.parametrize("succeed", [False, True])
@pytest.mark.parametrize("error_type", [LLMNetworkError, LLMTransientProviderError])
def test_default_budget_is_six_exponential_retries_even_after_text(
    monkeypatch: pytest.MonkeyPatch,
    succeed: bool,
    error_type: type[LLMNetworkError] | type[LLMTransientProviderError],
) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0
    delays: list[float] = []
    monkeypatch.setattr("llm_providers.client.time.sleep", delays.append)

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        yield LLMTextDelta(text=f"attempt-{calls}")
        if succeed and calls == 7:
            yield LLMTurnDone(stop_reason="end_turn")
        else:
            raise error_type("injected transient failure")

    monkeypatch.setattr(client, "_stream_once", attempt)
    stream = client._stream_with_network_retries(client.spec, [], allow_output_retries=True)
    retries = []
    try:
        for event in stream:
            if isinstance(event, LLMRetryScheduled):
                retries.append((event.attempt, event.max_attempts, event.delay_seconds))
    except (LLMNetworkError, LLMTransientProviderError):
        assert not succeed
    else:
        assert succeed
    assert calls == 7
    assert delays == [2, 4, 8, 16, 32, 64]
    assert retries == [(i, 7, delay) for i, delay in enumerate(delays, start=2)]


@pytest.mark.parametrize("succeed", [False, True])
def test_generate_uses_six_exponential_retries(
    monkeypatch: pytest.MonkeyPatch, succeed: bool
) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0
    delays: list[float] = []
    monkeypatch.setattr("llm_providers.client.time.sleep", delays.append)

    def attempt(*args: Any, **kwargs: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if succeed and calls == 7:
            return ModelResponse(text="recovered")
        raise LLMTransientProviderError("service unavailable", status_code=503)

    monkeypatch.setattr(client, "_call_once", attempt)
    request = ModelRequest(messages=[{"role": "user", "content": "hello"}])
    if succeed:
        assert client.generate(request).text == "recovered"
    else:
        with pytest.raises(LLMTransientProviderError):
            client.generate(request)
    assert calls == 7
    assert delays == [2, 4, 8, 16, 32, 64]


def test_default_backoff_can_recover_after_sixty_second_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    elapsed = 0.0
    attempts: list[float] = []

    def wait(delay: float) -> None:
        nonlocal elapsed
        elapsed += delay

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        attempts.append(elapsed)
        if elapsed < 60:
            raise LLMTransientProviderError("temporarily unavailable", status_code=503)
        yield LLMTurnDone(stop_reason="end_turn")

    monkeypatch.setattr("llm_providers.client.time.sleep", wait)
    monkeypatch.setattr(client, "_stream_once", attempt)
    events = list(client._stream_with_network_retries(client.spec, []))
    assert attempts == [0, 2, 6, 14, 30, 62]
    assert isinstance(events[-1], LLMTurnDone)


def test_engine_overload_keeps_five_retries_and_thirty_second_delay_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0
    delays: list[float] = []
    monkeypatch.setattr("llm_providers.client.time.sleep", delays.append)
    monkeypatch.setattr("llm_providers.client.random.uniform", lambda _low, _high: 0)

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        raise LLMTransientProviderError("overloaded", provider_error_type="server_is_overloaded")
        yield  # pragma: no cover

    monkeypatch.setattr(client, "_stream_once", attempt)
    retries: list[LLMRetryScheduled] = []
    with pytest.raises(LLMTransientProviderError):
        for event in client._stream_with_network_retries(client.spec, []):
            if isinstance(event, LLMRetryScheduled):
                retries.append(event)
    assert calls == 6
    assert delays == [2, 4, 8, 16, 30]
    assert [event.max_attempts for event in retries] == [6] * 5


def test_output_reset_requires_explicit_consumer_support(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        yield LLMTextDelta(text="already spoken")
        raise LLMNetworkError("disconnected")

    monkeypatch.setattr(client, "_stream_once", attempt)
    with pytest.raises(LLMStreamInterruptedAfterOutputError):
        list(client._stream_with_network_retries(client.spec, []))
    assert calls == 1


def test_auth_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        raise LLMAuthError("invalid key")
        yield  # pragma: no cover

    monkeypatch.setattr(client, "_stream_once", attempt)
    with pytest.raises(LLMAuthError):
        list(client._stream_with_network_retries(client.spec, [], allow_output_retries=True))
    assert calls == 1


def test_cancel_during_backoff_does_not_send_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        raise LLMNetworkError("disconnected")
        yield  # pragma: no cover

    def cancel() -> None:
        raise RuntimeError("cancelled")

    monkeypatch.setattr(client, "_stream_once", attempt)
    with pytest.raises(RuntimeError, match="cancelled"):
        list(client._stream_with_network_retries(client.spec, [], cancel_check=cancel))
    assert calls == 1


def test_long_server_cooldown_does_not_retry_early(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(ProviderSpec(model="deepseek/deepseek-chat", api_key="test"))
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        raise LLMTransientProviderError("cooldown", retry_after_seconds=3600)
        yield  # pragma: no cover

    monkeypatch.setattr(client, "_stream_once", attempt)
    with pytest.raises(LLMTransientProviderError, match="cooldown"):
        list(client._stream_with_network_retries(client.spec, []))
    assert calls == 1
