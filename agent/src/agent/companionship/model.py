"""A per-run model guard shared by conversation, compression and independent reviews."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

from llm_providers.stream_events import LLMStreamEvent, LLMTextDelta, LLMToolCallDelta, LLMTurnDone

from llm_providers import LanguageModel, ModelRequest, ModelResponse


class GovernedModel:
    def __init__(
        self,
        model: LanguageModel,
        *,
        record_call: Callable[[], None],
        check: Callable[[], None],
        submitted: Callable[[], None],
        usage: Callable[[object], None],
    ) -> None:
        self.model = model
        self.record_call = record_call
        self.check = check
        self.submitted = submitted
        self.usage = usage

    @property
    def context_window(self) -> int:
        return self.model.context_window

    def __getattr__(self, name: str) -> Any:
        return getattr(self.model, name)

    def _prepare(self, request: ModelRequest) -> ModelRequest:
        self.check()
        self.record_call()
        original = request.control.cancel_check

        def check() -> None:
            self.check()
            if original is not None:
                original()

        return replace(
            request,
            control=replace(
                request.control, cancel_check=check, allow_retries=False, retry_limit=0
            ),
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        result = self.model.generate(self._prepare(request))
        self.usage(result.usage)
        return result

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        stream = self.model.stream(self._prepare(request))
        submitted = False
        try:
            for event in stream:
                self.check()
                if (
                    not submitted
                    and request.control.trace_context is not None
                    and request.control.trace_context.operation == "conversation"
                    and isinstance(event, (LLMTextDelta, LLMToolCallDelta, LLMTurnDone))
                ):
                    self.submitted()
                    submitted = True
                if isinstance(event, LLMTurnDone):
                    self.usage(event.usage)
                yield event
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
