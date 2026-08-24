"""Cancellable async transport for providers routed through LiteLLM."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any, cast

import httpx
import litellm
import openai
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler

from .cancellable_stream import CancellableAsyncStream, open_cancellable_stream
from .request_witness import ProviderRequestCapture, ProviderRequestWitness
from .spec import ProviderSpec

_LITELLM_VERSION = version("litellm")


def _provider_id(spec: ProviderSpec) -> str:
    if spec.model.startswith("openrouter/"):
        return "openrouter"
    if spec.model.startswith("deepseek/") or spec.model.startswith("openai/deepseek-"):
        return "deepseek"
    return spec.model.split("/", 1)[0]


def _capture(spec: ProviderSpec) -> ProviderRequestCapture:
    return ProviderRequestCapture(
        provider_id=_provider_id(spec),
        transport="litellm",
        transform_identity=f"litellm:{_LITELLM_VERSION}:{spec.model}",
    )


def _attach_request_witness(
    error: BaseException,
    witness: ProviderRequestWitness | None,
) -> None:
    if witness is None:
        return
    with suppress(Exception):
        error.request_witness = witness  # type: ignore[attr-defined]


def _sync_client(spec: ProviderSpec, capture: ProviderRequestCapture) -> tuple[Any, Any]:
    http_client = httpx.Client(event_hooks={"request": [capture.observe]})
    if spec.model.startswith("openai/"):
        options: dict[str, Any] = {
            "api_key": spec.api_key,
            "max_retries": 0,
            "http_client": http_client,
        }
        if spec.api_base:
            options["base_url"] = spec.api_base
        client = openai.OpenAI(**options)
        return client, client
    handler = HTTPHandler(client=http_client)
    return handler, handler


def _async_client(spec: ProviderSpec, capture: ProviderRequestCapture) -> tuple[Any, Any]:
    if spec.model.startswith("openai/"):
        http_client = httpx.AsyncClient(event_hooks={"request": [capture.observe_async]})
        options: dict[str, Any] = {
            "api_key": spec.api_key,
            "max_retries": 0,
            "http_client": http_client,
        }
        if spec.api_base:
            options["base_url"] = spec.api_base
        client = openai.AsyncOpenAI(**options)
        return client, client
    handler = AsyncHTTPHandler(event_hooks={"request": [capture.observe_async]})
    return handler, handler


@dataclass
class LiteLLMStream:
    """Own one sync LiteLLM stream and its per-call instrumented client."""

    response: Any
    owner: Any
    request_witness: ProviderRequestWitness | None
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
            close_owner = getattr(self.owner, "close", None)
            if callable(close_owner):
                close_owner()


def open_litellm_stream(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
) -> LiteLLMStream:
    """Open a sync stream with a per-call final-request witness."""

    options = dict(request_options)
    options.pop("client", None)
    capture = _capture(spec)
    owner, client = _sync_client(spec, capture)
    try:
        response = litellm.completion(
            model=spec.model,
            api_key=spec.api_key,
            api_base=spec.api_base,
            messages=messages,
            stream=True,
            client=client,
            **options,
        )
    except BaseException as exc:
        _attach_request_witness(exc, capture.witness)
        close_owner = getattr(owner, "close", None)
        if callable(close_owner):
            close_owner()
        raise
    return LiteLLMStream(
        response=response,
        owner=owner,
        request_witness=capture.witness,
        request_id=getattr(response, "request_id", None),
    )


@dataclass
class LiteLLMAsyncStreamSource:
    """Adapt ``litellm.acompletion`` to the provider-neutral stream contract."""

    spec: ProviderSpec
    messages: list[dict[str, Any]]
    request_options: dict[str, Any]
    request_id: str | None = field(default=None, init=False)
    request_witness: ProviderRequestWitness | None = field(default=None, init=False)
    _capture: ProviderRequestCapture = field(init=False, repr=False)
    _response: Any = field(default=None, init=False)
    _owner: Any = field(default=None, init=False)
    _client: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        # Client construction is local CPU/setup work, not provider header idle.
        self._capture = _capture(self.spec)
        self._owner, self._client = _async_client(self.spec, self._capture)

    async def open(self) -> AsyncIterator[Any]:
        options = dict(self.request_options)
        options.pop("client", None)
        try:
            self._response = await litellm.acompletion(
                model=self.spec.model,
                api_key=self.spec.api_key,
                api_base=self.spec.api_base,
                messages=self.messages,
                stream=True,
                client=self._client,
                **options,
            )
        except BaseException as exc:
            _attach_request_witness(exc, self._capture.witness)
            raise
        self.request_witness = self._capture.witness
        self.request_id = getattr(self._response, "request_id", None)
        return cast(AsyncIterator[Any], self._response)

    async def close(self) -> None:
        response = self._response
        self._response = None
        owner = self._owner
        self._owner = None
        self._client = None
        try:
            if response is not None:
                close = getattr(response, "aclose", None)
                if not callable(close):
                    close = getattr(response, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
        finally:
            if owner is not None:
                close_owner = getattr(owner, "close", None)
                if callable(close_owner):
                    result = close_owner()
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
