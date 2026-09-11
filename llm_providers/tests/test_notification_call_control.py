from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from llm_providers import LLMClient, LLMNetworkError, ModelCallControl, ModelRequest, ProviderSpec


def test_call_control_disables_stream_retries_without_mutating_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(
        ProviderSpec(model="deepseek/deepseek-flash", api_key="fake", context_window=10000),
        network_retries=2,
        network_retry_backoff_seconds=0,
    )
    attempts: list[int] = []

    def fail(*args: Any, **kwargs: Any) -> Iterator[Any]:
        attempts.append(1)
        raise LLMNetworkError("offline")
        yield  # pragma: no cover

    monkeypatch.setattr(client, "_stream_once", fail)
    with pytest.raises(LLMNetworkError):
        list(
            client.stream(
                ModelRequest(
                    messages=[{"role": "user", "content": "notice"}],
                    control=ModelCallControl(allow_retries=False),
                )
            )
        )
    assert len(attempts) == 1
    attempts.clear()
    with pytest.raises(LLMNetworkError):
        list(client.stream(ModelRequest(messages=[{"role": "user", "content": "normal"}])))
    assert len(attempts) == 3
