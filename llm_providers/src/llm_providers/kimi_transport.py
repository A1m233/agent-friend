"""Kimi-specific streaming transport.

Kimi's OpenAI-compatible SSE can attach usage to ``choices[0].usage``.  LiteLLM's
generic OpenAI stream wrapper currently only promotes top-level ``chunk.usage``,
so provider-specific cache counters may be lost before :mod:`llm_providers`
normalizes them.  Keep that compatibility at the provider boundary by using the
OpenAI SDK directly for canonical Kimi models.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import openai

from .cancellable_stream import CancellableAsyncStream, open_cancellable_stream
from .spec import ProviderSpec


def uses_kimi_stream_transport(spec: ProviderSpec) -> bool:
    """Return whether ``spec`` targets a canonical Kimi chat model."""
    return spec.model.startswith("openai/kimi-")


@dataclass
class KimiStream:
    """Own the SDK stream and client so both are closed deterministically."""

    response: Any
    client: openai.OpenAI
    request_id: str | None = None
    _closed: bool = field(default=False, init=False)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.response)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close_response = getattr(self.response, "close", None)
            if callable(close_response):
                close_response()
        finally:
            self.client.close()


@dataclass
class KimiAsyncStreamSource:
    """Own Kimi's direct SDK request while using the shared cancellation bridge."""

    spec: ProviderSpec
    messages: list[dict[str, Any]]
    request_options: dict[str, Any]
    request_id: str | None = field(default=None, init=False)
    _client: openai.AsyncOpenAI | None = field(default=None, init=False)
    _response: Any = field(default=None, init=False)

    async def open(self) -> AsyncIterator[Any]:
        self._client = openai.AsyncOpenAI(
            api_key=self.spec.api_key,
            base_url=self.spec.api_base,
            max_retries=0,
        )
        raw_response = await self._client.chat.completions.with_raw_response.create(
            model=self.spec.model.removeprefix("openai/"),
            messages=self.messages,  # type: ignore[arg-type]
            stream=True,
            **self.request_options,
        )
        self._response = raw_response.parse()
        self.request_id = getattr(raw_response, "request_id", None)
        return cast(AsyncIterator[Any], self._response)

    async def close(self) -> None:
        response = self._response
        self._response = None
        client = self._client
        self._client = None
        try:
            if response is not None:
                await response.close()
        finally:
            if client is not None:
                await client.close()


def open_kimi_stream(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
) -> KimiStream:
    """Open a Kimi stream without passing provider chunks through LiteLLM."""
    if not spec.api_base:
        raise ValueError("Kimi stream transport requires an explicit api_base")

    options = dict(request_options)
    options.pop("max_retries", None)
    client = openai.OpenAI(
        api_key=spec.api_key,
        base_url=spec.api_base,
        max_retries=0,
    )
    try:
        raw_response = client.chat.completions.with_raw_response.create(
            model=spec.model.removeprefix("openai/"),
            messages=messages,  # type: ignore[arg-type]
            stream=True,
            **options,
        )
        response = raw_response.parse()
    except Exception:
        client.close()
        raise
    return KimiStream(
        response=response,
        client=client,
        request_id=getattr(raw_response, "request_id", None),
    )


def open_cancellable_kimi_stream(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
    cancel_check: Callable[[], None],
    *,
    timeout_seconds: float | None = None,
) -> CancellableAsyncStream:
    """Open a Kimi stream whose response-header wait can be explicitly cancelled."""
    if not spec.api_base:
        raise ValueError("Kimi stream transport requires an explicit api_base")

    options = dict(request_options)
    options.pop("max_retries", None)
    return open_cancellable_stream(
        KimiAsyncStreamSource(spec, messages, options),
        cancel_check,
        timeout_seconds=timeout_seconds,
        thread_name="kimi-cancellable-stream",
    )
