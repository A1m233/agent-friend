"""Project-owned language-model call contract.

Agent orchestration depends on these types instead of a concrete provider SDK or
transport implementation.  The contract intentionally mirrors the synchronous
runtime used by agent-friend today; transport adapters may bridge to async APIs
internally without leaking that concurrency detail upward.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .request_witness import ProviderRequestWitness
from .stream_events import (
    LLMResponseDiagnostics,
    LLMStreamEvent,
    LLMTransportDiagnostic,
    LLMUsage,
)

if TYPE_CHECKING:
    from .catalog import ModelRuntimeConfig


@dataclass(frozen=True)
class ModelCallTraceContext:
    """Content-free runtime identity attached to one logical model call."""

    operation: str = "unspecified"
    surface: str | None = None
    run_id: str | None = field(default=None, repr=False, compare=False)
    session_id: str | None = field(default=None, repr=False, compare=False)
    call_kind: str | None = None
    call_index: int | None = None
    turn_index: int | None = None
    recovery_attempt: int | None = None
    comparison_scope: str = "unavailable"
    comparison_precision: str = "unavailable"
    comparison: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)
    adapter_trace: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ModelCallControl:
    """Non-provider control signals for one model request."""

    cancel_check: Callable[[], None] | None = None
    diagnostic_callback: Callable[[LLMTransportDiagnostic], None] | None = None
    previous_request_witness: ProviderRequestWitness | None = None
    continuation_disposition: str = "baseline"
    rebase_reason: str | None = None
    trace_context: ModelCallTraceContext | None = None


@dataclass(frozen=True)
class ModelRequest:
    """Normalized request accepted by every language-model implementation."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)
    cache_key: str | None = None
    control: ModelCallControl = field(default_factory=ModelCallControl)


@dataclass(frozen=True)
class ModelResponse:
    """Provider-neutral result for a non-streaming model request."""

    text: str
    usage: LLMUsage | None = None
    diagnostics: LLMResponseDiagnostics | None = None


class LanguageModel(Protocol):
    """Small model port consumed by Agent, Context and Memory."""

    @property
    def context_window(self) -> int:
        """Maximum input context for the bound product model."""

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Return one complete response."""

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        """Yield normalized events for one model response."""


LanguageModelFactory = Callable[["ModelRuntimeConfig"], LanguageModel]


__all__ = [
    "LanguageModel",
    "LanguageModelFactory",
    "ModelCallControl",
    "ModelCallTraceContext",
    "ModelRequest",
    "ModelResponse",
]
