"""ASR provider protocol types.

The WebSocket route owns the project-level protocol. Provider implementations
hide vendor-specific authentication, frame formats and response parsing behind
this small async interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class AsrStartOptions:
    """Options negotiated by the frontend before streaming audio."""

    trace_id: str
    audio_format: str
    sample_rate: int
    channels: int
    locale: str | None = None


@dataclass(frozen=True)
class AsrTranscriptEvent:
    """Provider transcript event surfaced to the frontend."""

    type: Literal["partial", "final"]
    text: str


@dataclass(frozen=True)
class AsrPrewarmOptions:
    """Options for opening a provider connection before audio is attached."""

    trace_id: str
    reason: str


@dataclass(frozen=True)
class AsrPrewarmResult:
    """Result of a provider prewarm attempt."""

    status: Literal["started", "already_warm", "disabled", "unavailable", "error"]
    trace_id: str
    ttl_ms: int
    warm_age_ms: int | None = None
    message: str | None = None


class AsrProviderError(Exception):
    """ASR provider failure that can be safely shown as a user-facing code/message."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(detail or message)
        self.code = code
        self.message = message
        self.detail = detail


class AsrProviderUnavailableError(AsrProviderError):
    """Raised when no usable ASR provider is configured for voice input."""

    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__(
            "asr_provider_unavailable",
            "语音输入识别服务还没有接上，请稍后再试",
            detail=detail,
        )


class AsrSession(Protocol):
    """A single streaming ASR session."""

    async def send_audio(self, chunk: bytes) -> None:
        """Send one audio chunk to the provider."""

    async def finish(self) -> None:
        """Request final recognition and graceful provider shutdown."""

    async def cancel(self) -> None:
        """Abort the session without waiting for final recognition."""

    def events(self) -> AsyncIterator[AsrTranscriptEvent]:
        """Yield partial/final transcript events until the session ends."""


class AsrProvider(Protocol):
    """Factory for ASR sessions."""

    async def start(self, options: AsrStartOptions) -> AsrSession:
        """Start a provider session for one frontend WebSocket."""


class AsrPrewarmProvider(Protocol):
    """Optional provider capability for connect-only warmup."""

    async def prewarm(self, options: AsrPrewarmOptions) -> AsrPrewarmResult:
        """Open a warm provider connection without starting recognition."""
