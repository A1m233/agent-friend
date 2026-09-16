"""Deterministic transport failures: no SDK, sockets or real model calls."""

from collections.abc import Iterator
from typing import Any

import pytest
from llm_providers.errors import (
    LLMAuthError,
    LLMStreamInterruptedAfterOutputError,
    LLMTransientProviderError,
)

from llm_providers import (
    LLMClient,
    LLMNetworkError,
    LLMRetryScheduled,
    LLMTextDelta,
    LLMTurnDone,
    ProviderSpec,
)


@pytest.mark.parametrize("succeed", [False, True])
def test_default_budget_is_five_retries_even_after_text(
    monkeypatch: pytest.MonkeyPatch, succeed: bool
) -> None:
    client = LLMClient(
        ProviderSpec(model="deepseek/deepseek-chat", api_key="test"),
        network_retry_backoff_seconds=0,
    )
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal calls
        calls += 1
        yield LLMTextDelta(text=f"attempt-{calls}")
        if succeed and calls == 6:
            yield LLMTurnDone(stop_reason="end_turn")
        else:
            raise LLMNetworkError("injected disconnect")

    monkeypatch.setattr(client, "_stream_once", attempt)
    stream = client._stream_with_network_retries(client.spec, [], allow_output_retries=True)
    retries = []
    try:
        for event in stream:
            if isinstance(event, LLMRetryScheduled):
                retries.append((event.attempt, event.max_attempts))
    except LLMNetworkError:
        assert not succeed
    else:
        assert succeed
    assert calls == 6
    assert retries == [(i, 6) for i in range(2, 7)]


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
