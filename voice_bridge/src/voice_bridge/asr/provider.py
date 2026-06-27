"""Default ASR provider implementations."""

from __future__ import annotations

from .types import (
    AsrPrewarmOptions,
    AsrPrewarmResult,
    AsrProviderUnavailableError,
    AsrSession,
    AsrStartOptions,
)


class DisabledAsrProvider:
    """Provider used when streaming ASR has not been configured yet.

    It keeps the route and frontend behavior explicit: voice_bridge accepts the
    voice-input WebSocket, logs the trace, then returns a stable user-facing
    error instead of pretending that transcription is available.
    """

    async def start(self, options: AsrStartOptions) -> AsrSession:
        raise AsrProviderUnavailableError(
            detail=(
                f"ASR provider is disabled; trace_id={options.trace_id} "
                f"format={options.audio_format} sample_rate={options.sample_rate} "
                f"channels={options.channels}"
            )
        )

    async def prewarm(self, options: AsrPrewarmOptions) -> AsrPrewarmResult:
        return AsrPrewarmResult(
            status="disabled",
            trace_id=options.trace_id,
            ttl_ms=0,
            message="ASR provider is disabled",
        )
