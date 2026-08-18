"""Cancellable async transport for providers routed through LiteLLM."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import litellm

from .cancellable_stream import CancellableAsyncStream, open_cancellable_stream
from .spec import ProviderSpec


@dataclass
class LiteLLMAsyncStreamSource:
    """Adapt ``litellm.acompletion`` to the provider-neutral stream contract."""

    spec: ProviderSpec
    messages: list[dict[str, Any]]
    request_options: dict[str, Any]
    request_id: str | None = field(default=None, init=False)
    _response: Any = field(default=None, init=False)

    async def open(self) -> AsyncIterator[Any]:
        self._response = await litellm.acompletion(
            model=self.spec.model,
            api_key=self.spec.api_key,
            api_base=self.spec.api_base,
            messages=self.messages,
            stream=True,
            **self.request_options,
        )
        self.request_id = getattr(self._response, "request_id", None)
        return cast(AsyncIterator[Any], self._response)

    async def close(self) -> None:
        response = self._response
        self._response = None
        if response is None:
            return
        close = getattr(response, "aclose", None)
        if not callable(close):
            close = getattr(response, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result


def open_cancellable_litellm_stream(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
    cancel_check: Callable[[], None],
    *,
    timeout_seconds: float | None = None,
) -> CancellableAsyncStream:
    """Open any LiteLLM-backed provider with interruptible header waiting."""
    return open_cancellable_stream(
        LiteLLMAsyncStreamSource(spec, messages, dict(request_options)),
        cancel_check,
        timeout_seconds=timeout_seconds,
        thread_name="litellm-cancellable-stream",
    )
