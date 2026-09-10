"""Direct Responses SDK source owned by the existing cancellable stream bridge."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from importlib.metadata import version
from typing import Any, cast

import httpx
import openai

from ..cancellable_stream import CancellableAsyncStream, open_cancellable_stream
from ..request_witness import ProviderRequestCapture, ProviderRequestWitness
from ..spec import ProviderSpec


class ResponsesSource:
    def __init__(self, spec: ProviderSpec, request: dict[str, Any]) -> None:
        assert spec.responses is not None
        self.request_id: str | None = None
        self.request_witness: ProviderRequestWitness | None = None
        self.capture = ProviderRequestCapture(
            provider_id=spec.provider_id or "custom",
            transport="responses",
            transform_identity=f"openai:{version('openai')}:{spec.responses.model}",
        )
        self.client = openai.AsyncOpenAI(
            api_key=spec.api_key,
            base_url=spec.api_base or spec.responses.api_base,
            max_retries=0,
            http_client=httpx.AsyncClient(event_hooks={"request": [self.capture.observe_async]}),
        )
        self.request = request
        self.response: Any = None

    async def open(self) -> AsyncIterator[Any]:
        try:
            raw = await self.client.responses.with_raw_response.create(**self.request)
            self.response = raw.parse()
            self.request_id = getattr(raw, "request_id", None)
            if self.request.get("stream") is False:
                return self._completed_response()
            return cast(AsyncIterator[Any], self.response)
        except BaseException as error:
            with suppress(Exception):
                error.request_witness = self.capture.witness  # type: ignore[attr-defined]
            raise
        finally:
            self.request_witness = self.capture.witness

    async def _completed_response(self) -> AsyncIterator[Any]:
        # Reuse item/terminal validation without pretending the HTTP response was SSE.
        response = self.response.model_dump(mode="json")
        yield {"type": f"response.{response.get('status')}", "response": response}

    async def close(self) -> None:
        try:
            if self.response is not None and self.request.get("stream") is not False:
                await self.response.close()
        finally:
            await self.client.close()


def open_responses_stream(
    spec: ProviderSpec,
    request: dict[str, Any],
    cancel_check: Callable[[], None] | None,
    timeout_seconds: float,
) -> CancellableAsyncStream:
    return open_cancellable_stream(
        ResponsesSource(spec, request),
        cancel_check or (lambda: None),
        timeout_seconds=timeout_seconds,
        thread_name="llm-responses-stream",
    )
