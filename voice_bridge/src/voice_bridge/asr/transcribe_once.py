"""One-shot transcription helper built on the streaming ASR provider."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from .types import (
    AsrProvider,
    AsrProviderError,
    AsrSession,
    AsrStartOptions,
)

_DEFAULT_CHUNK_BYTES = 32 * 1024


@dataclass(frozen=True)
class OneShotTranscriptionOptions:
    trace_id: str
    sample_rate: int
    channels: int
    locale: str | None = None
    chunk_bytes: int = _DEFAULT_CHUNK_BYTES


@dataclass(frozen=True)
class OneShotTranscriptionResult:
    trace_id: str
    text: str
    used_partial_fallback: bool = False


async def transcribe_pcm16_once(
    provider: AsrProvider,
    audio: bytes,
    options: OneShotTranscriptionOptions,
) -> OneShotTranscriptionResult:
    """Transcribe a complete PCM16 audio buffer through the streaming provider."""

    if not audio:
        raise AsrProviderError(
            "transcription_failed",
            "语音识别暂时没有得到结果，请稍后再试",
            detail=f"empty audio buffer trace_id={options.trace_id}",
        )
    if options.chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")

    session: AsrSession | None = None
    try:
        session = await provider.start(
            AsrStartOptions(
                trace_id=options.trace_id,
                audio_format="pcm16",
                sample_rate=options.sample_rate,
                channels=options.channels,
                locale=options.locale,
            )
        )
        for offset in range(0, len(audio), options.chunk_bytes):
            await session.send_audio(audio[offset : offset + options.chunk_bytes])
        await session.finish()

        final_text = ""
        last_partial_text = ""
        async for event in session.events():
            text = event.text.strip()
            if not text:
                continue
            if event.type == "final":
                final_text = text
            elif event.type == "partial":
                last_partial_text = text

        text = final_text or last_partial_text
        if not text:
            raise AsrProviderError(
                "transcription_failed",
                "语音识别暂时没有得到结果，请稍后再试",
                detail=f"no transcript events trace_id={options.trace_id}",
            )
        return OneShotTranscriptionResult(
            trace_id=options.trace_id,
            text=text,
            used_partial_fallback=not bool(final_text),
        )
    except AsrProviderError:
        raise
    except Exception as e:
        if session is not None:
            with suppress(Exception):
                await session.cancel()
        raise AsrProviderError(
            "transcription_failed",
            "语音识别暂时失败，请稍后再试",
            detail=f"{type(e).__name__}: {e}",
        ) from e
