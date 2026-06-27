"""Volc streaming ASR provider.

Protocol source: Volcengine "流式语音识别WebSocket" official documentation
(``/docs/6561/1354869``). The provider translates the frontend's plain PCM16
chunks into Volc's binary WebSocket protocol and surfaces text events through
the project-level ASR provider interface.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import connect

from ..latency import log_latency, monotonic_ms
from ..settings import VoiceBridgeSettings
from .types import (
    AsrPrewarmOptions,
    AsrPrewarmResult,
    AsrProviderError,
    AsrProviderUnavailableError,
    AsrSession,
    AsrStartOptions,
    AsrTranscriptEvent,
)

_PROTOCOL_VERSION = 0b0001
_HEADER_SIZE_WORDS = 0b0001

_MSG_FULL_CLIENT_REQUEST = 0b0001
_MSG_AUDIO_ONLY_REQUEST = 0b0010
_MSG_FULL_SERVER_RESPONSE = 0b1001
_MSG_ERROR = 0b1111

_FLAG_NONE = 0b0000
_FLAG_POS_SEQUENCE = 0b0001
_FLAG_LAST_NO_SEQUENCE = 0b0010
_FLAG_NEG_SEQUENCE = 0b0011

_SERIALIZATION_NONE = 0b0000
_SERIALIZATION_JSON = 0b0001

_COMPRESSION_NONE = 0b0000
_COMPRESSION_GZIP = 0b0001

ConnectFactory = Callable[..., Awaitable[Any]]

logger = logging.getLogger(__name__)


@dataclass
class _WarmVolcConnection:
    websocket: Any
    trace_id: str
    created_ms: int


class VolcAsrProvider:
    """Direct Volc streaming ASR adapter."""

    def __init__(
        self,
        settings: VoiceBridgeSettings,
        *,
        connect_factory: ConnectFactory = connect,
    ) -> None:
        self._settings = settings
        self._connect = connect_factory
        self._warm_lock = asyncio.Lock()
        self._warm: _WarmVolcConnection | None = None
        self._warm_expire_task: asyncio.Task[None] | None = None

    async def prewarm(self, options: AsrPrewarmOptions) -> AsrPrewarmResult:
        ttl_ms = self._settings.voice_input_prewarm_ttl_ms
        started_ms = monotonic_ms()
        if not self._settings.voice_input_prewarm_enabled:
            log_latency(
                logger,
                "voice_input_prewarm_skipped",
                trace_id=options.trace_id,
                reason=options.reason,
                status="disabled",
            )
            return AsrPrewarmResult(status="disabled", trace_id=options.trace_id, ttl_ms=0)

        async with self._warm_lock:
            now_ms = monotonic_ms()
            if self._warm is not None:
                warm_age_ms = now_ms - self._warm.created_ms
                if warm_age_ms < ttl_ms:
                    self._warm.created_ms = now_ms
                    self._schedule_warm_expiry_locked(self._warm.trace_id, ttl_ms)
                    log_latency(
                        logger,
                        "voice_input_prewarm_reused",
                        trace_id=options.trace_id,
                        warm_trace_id=self._warm.trace_id,
                        reason=options.reason,
                        warm_age_ms=warm_age_ms,
                        ttl_ms=ttl_ms,
                    )
                    return AsrPrewarmResult(
                        status="already_warm",
                        trace_id=options.trace_id,
                        ttl_ms=ttl_ms,
                        warm_age_ms=warm_age_ms,
                    )
                await self._close_warm_locked(cancel_expiry=True)

            log_latency(
                logger,
                "voice_input_prewarm_connect_start",
                trace_id=options.trace_id,
                reason=options.reason,
                ttl_ms=ttl_ms,
            )
            try:
                websocket = await self._connect_new(options.trace_id)
            except AsrProviderUnavailableError as e:
                log_latency(
                    logger,
                    "voice_input_prewarm_result",
                    trace_id=options.trace_id,
                    reason=options.reason,
                    status="unavailable",
                    elapsed_ms=monotonic_ms() - started_ms,
                )
                return AsrPrewarmResult(
                    status="unavailable",
                    trace_id=options.trace_id,
                    ttl_ms=ttl_ms,
                    message=e.detail or e.message,
                )
            except Exception as e:
                log_latency(
                    logger,
                    "voice_input_prewarm_result",
                    trace_id=options.trace_id,
                    reason=options.reason,
                    status="error",
                    elapsed_ms=monotonic_ms() - started_ms,
                )
                logger.warning("voice input prewarm failed trace_id=%s: %s", options.trace_id, e)
                return AsrPrewarmResult(
                    status="error",
                    trace_id=options.trace_id,
                    ttl_ms=ttl_ms,
                    message=str(e),
                )

            self._warm = _WarmVolcConnection(
                websocket=websocket,
                trace_id=options.trace_id,
                created_ms=monotonic_ms(),
            )
            self._schedule_warm_expiry_locked(options.trace_id, ttl_ms)
            log_latency(
                logger,
                "voice_input_prewarm_result",
                trace_id=options.trace_id,
                reason=options.reason,
                status="started",
                ttl_ms=ttl_ms,
                elapsed_ms=monotonic_ms() - started_ms,
            )
            return AsrPrewarmResult(status="started", trace_id=options.trace_id, ttl_ms=ttl_ms)

    async def start(self, options: AsrStartOptions) -> AsrSession:
        warm_websocket = await self._take_warm_websocket(options.trace_id)
        if warm_websocket is not None:
            try:
                return await self._start_on_websocket(warm_websocket, options)
            except AsrProviderError:
                raise
            except Exception as e:
                log_latency(
                    logger,
                    "voice_input_prewarm_consume_failed",
                    trace_id=options.trace_id,
                    detail=type(e).__name__,
                )
                logger.warning(
                    "voice input warm ASR socket failed; falling back to cold connect trace_id=%s",
                    options.trace_id,
                )

        return await self._start_cold(options)

    async def _start_on_websocket(self, websocket: Any, options: AsrStartOptions) -> AsrSession:
        session = VolcAsrSession(websocket, options)
        try:
            await session.start()
        except Exception:
            await session.cancel()
            raise
        return session

    async def _connect_new(self, trace_id: str) -> Any:
        headers = build_volc_headers(self._settings, trace_id)
        return await self._connect(
            self._settings.volc_speech_ws_url,
            additional_headers=headers,
            compression=None,
            open_timeout=10,
            ping_interval=20,
        )

    async def _take_warm_websocket(self, trace_id: str) -> Any | None:
        ttl_ms = self._settings.voice_input_prewarm_ttl_ms
        async with self._warm_lock:
            if self._warm is None:
                return None
            warm_age_ms = monotonic_ms() - self._warm.created_ms
            if warm_age_ms >= ttl_ms:
                log_latency(
                    logger,
                    "voice_input_prewarm_expired_before_consume",
                    trace_id=trace_id,
                    warm_trace_id=self._warm.trace_id,
                    warm_age_ms=warm_age_ms,
                    ttl_ms=ttl_ms,
                )
                await self._close_warm_locked(cancel_expiry=True)
                return None

            warm = self._warm
            self._warm = None
            self._cancel_warm_expiry_locked()
            log_latency(
                logger,
                "voice_input_prewarm_consumed",
                trace_id=trace_id,
                warm_trace_id=warm.trace_id,
                warm_age_ms=warm_age_ms,
                ttl_ms=ttl_ms,
            )
            return warm.websocket

    def _schedule_warm_expiry_locked(self, trace_id: str, ttl_ms: int) -> None:
        self._cancel_warm_expiry_locked()
        self._warm_expire_task = asyncio.create_task(self._expire_warm_after(trace_id, ttl_ms))

    def _cancel_warm_expiry_locked(self) -> None:
        if self._warm_expire_task is None:
            return
        self._warm_expire_task.cancel()
        self._warm_expire_task = None

    async def _expire_warm_after(self, trace_id: str, ttl_ms: int) -> None:
        try:
            await asyncio.sleep(ttl_ms / 1000)
            async with self._warm_lock:
                if self._warm is None or self._warm.trace_id != trace_id:
                    return
                warm_age_ms = monotonic_ms() - self._warm.created_ms
                log_latency(
                    logger,
                    "voice_input_prewarm_expired",
                    trace_id=trace_id,
                    warm_age_ms=warm_age_ms,
                    ttl_ms=ttl_ms,
                )
                await self._close_warm_locked(cancel_expiry=False)
                self._warm_expire_task = None
        except asyncio.CancelledError:
            raise

    async def _close_warm_locked(self, *, cancel_expiry: bool) -> None:
        warm = self._warm
        self._warm = None
        if cancel_expiry:
            self._cancel_warm_expiry_locked()
        if warm is not None:
            await _close_websocket(warm.websocket)

    async def _start_cold(self, options: AsrStartOptions) -> AsrSession:
        headers = build_volc_headers(self._settings, options.trace_id)
        try:
            websocket = await self._connect(
                self._settings.volc_speech_ws_url,
                additional_headers=headers,
                compression=None,
                open_timeout=10,
                ping_interval=20,
            )
            session = VolcAsrSession(websocket, options)
            await session.start()
            return session
        except AsrProviderError:
            raise
        except OSError as e:
            raise AsrProviderError(
                "volc_asr_unreachable",
                "语音识别服务暂时连接不上，请稍后再试",
                detail=str(e),
            ) from e
        except Exception as e:
            raise AsrProviderError(
                "volc_asr_start_failed",
                "语音识别服务启动失败，请稍后再试",
                detail=str(e),
            ) from e


class VolcAsrSession:
    """One Volc WebSocket ASR session."""

    def __init__(self, websocket: Any, options: AsrStartOptions) -> None:
        self._websocket = websocket
        self._options = options
        self._pending_audio: bytes | None = None
        self._last_text = ""
        self._closed = False

    async def start(self) -> None:
        await self._websocket.send(build_full_client_request(self._options))

    async def send_audio(self, chunk: bytes) -> None:
        if not chunk or self._closed:
            return
        if self._pending_audio is not None:
            await self._websocket.send(build_audio_request(self._pending_audio, is_last=False))
        self._pending_audio = chunk

    async def finish(self) -> None:
        if self._closed:
            return
        chunk = self._pending_audio or b""
        self._pending_audio = None
        await self._websocket.send(build_audio_request(chunk, is_last=True))

    async def cancel(self) -> None:
        await self._close()

    async def events(self) -> AsyncIterator[AsrTranscriptEvent]:
        try:
            while not self._closed:
                raw = await self._websocket.recv()
                if not isinstance(raw, bytes):
                    continue
                parsed = parse_volc_frame(raw)
                if parsed.message_type == _MSG_ERROR:
                    raise AsrProviderError(
                        "volc_asr_error",
                        "语音识别服务返回错误，请稍后再试",
                        detail=str(parsed.payload),
                    )
                if parsed.message_type != _MSG_FULL_SERVER_RESPONSE:
                    continue
                text = extract_transcript_text(parsed.payload)
                if not text:
                    if parsed.is_last:
                        break
                    continue
                if parsed.is_last:
                    event = AsrTranscriptEvent(type="final", text=text)
                else:
                    event = AsrTranscriptEvent(type="partial", text=text)
                if event.type == "partial" and text == self._last_text:
                    continue
                self._last_text = text
                yield event
                if parsed.is_last:
                    break
        except AsrProviderError:
            raise
        except Exception as e:
            if not self._closed:
                raise AsrProviderError(
                    "volc_asr_stream_failed",
                    "语音识别过程中断了，请稍后再试",
                    detail=str(e),
                ) from e
        finally:
            await self._close()

    async def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._websocket, "close", None)
        if close is not None:
            await close()


async def _close_websocket(websocket: Any) -> None:
    close = getattr(websocket, "close", None)
    if close is None:
        return
    with suppress(Exception):
        result = close()
        if isawaitable(result):
            await result


class VolcFrame:
    def __init__(self, message_type: int, flags: int, payload: Any) -> None:
        self.message_type = message_type
        self.flags = flags
        self.payload = payload

    @property
    def is_last(self) -> bool:
        return self.flags in (_FLAG_LAST_NO_SEQUENCE, _FLAG_NEG_SEQUENCE)


def build_volc_headers(settings: VoiceBridgeSettings, trace_id: str) -> dict[str, str]:
    """Build Volc handshake headers without logging credential values."""
    if settings.volc_speech_api_key:
        auth_headers = {"X-Api-Key": settings.volc_speech_api_key}
    elif settings.volc_speech_app_id and settings.volc_speech_access_token:
        auth_headers = {
            "X-Api-App-Key": settings.volc_speech_app_id,
            "X-Api-Access-Key": settings.volc_speech_access_token,
        }
    else:
        raise AsrProviderUnavailableError(detail="VOLC_SPEECH credentials are missing")

    request_id = trace_id or str(uuid4())
    return {
        **auth_headers,
        "X-Api-Resource-Id": settings.volc_speech_resource_id,
        "X-Api-Request-Id": request_id,
        "X-Api-Connect-Id": request_id,
        "X-Api-Sequence": "-1",
    }


def build_full_client_request(options: AsrStartOptions) -> bytes:
    payload = {
        "user": {"uid": "agent-friend"},
        "audio": {
            "format": _to_volc_audio_format(options.audio_format),
            "codec": "raw",
            "rate": options.sample_rate,
            "bits": 16,
            "channel": options.channels,
            "language": options.locale or "zh-CN",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": True,
            "result_type": "full",
        },
    }
    return _build_frame(
        message_type=_MSG_FULL_CLIENT_REQUEST,
        flags=_FLAG_NONE,
        serialization=_SERIALIZATION_JSON,
        compression=_COMPRESSION_GZIP,
        payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def build_audio_request(chunk: bytes, *, is_last: bool) -> bytes:
    return _build_frame(
        message_type=_MSG_AUDIO_ONLY_REQUEST,
        flags=_FLAG_LAST_NO_SEQUENCE if is_last else _FLAG_NONE,
        serialization=_SERIALIZATION_NONE,
        compression=_COMPRESSION_GZIP,
        payload=chunk,
    )


def parse_volc_frame(frame: bytes) -> VolcFrame:
    if len(frame) < 8:
        raise AsrProviderError("volc_asr_bad_frame", "语音识别服务返回格式异常")
    header_size = (frame[0] & 0x0F) * 4
    message_type = frame[1] >> 4
    flags = frame[1] & 0x0F
    serialization = frame[2] >> 4
    compression = frame[2] & 0x0F
    offset = header_size

    if message_type == _MSG_ERROR:
        if len(frame) < offset + 8:
            raise AsrProviderError("volc_asr_bad_error_frame", "语音识别服务返回错误格式异常")
        error_code = int.from_bytes(frame[offset : offset + 4], "big", signed=False)
        offset += 4
        error_size = int.from_bytes(frame[offset : offset + 4], "big", signed=False)
        offset += 4
        error_message = frame[offset : offset + error_size].decode("utf-8", errors="replace")
        return VolcFrame(message_type, flags, {"code": error_code, "message": error_message})

    if flags in (_FLAG_POS_SEQUENCE, _FLAG_NEG_SEQUENCE):
        offset += 4
    if len(frame) < offset + 4:
        raise AsrProviderError("volc_asr_bad_frame", "语音识别服务返回格式异常")
    payload_size = int.from_bytes(frame[offset : offset + 4], "big", signed=False)
    offset += 4
    payload = frame[offset : offset + payload_size]
    if compression == _COMPRESSION_GZIP and payload:
        payload = gzip.decompress(payload)
    if serialization == _SERIALIZATION_JSON and payload:
        parsed_payload: Any = json.loads(payload.decode("utf-8"))
    else:
        parsed_payload = payload
    return VolcFrame(message_type, flags, parsed_payload)


def extract_transcript_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(result, list):
        parts = [item.get("text", "") for item in result if isinstance(item, dict)]
        return "".join(part for part in parts if isinstance(part, str))
    return ""


def _build_frame(
    *,
    message_type: int,
    flags: int,
    serialization: int,
    compression: int,
    payload: bytes,
) -> bytes:
    packed_payload = gzip.compress(payload) if compression == _COMPRESSION_GZIP else payload
    header = bytes(
        [
            (_PROTOCOL_VERSION << 4) | _HEADER_SIZE_WORDS,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        ]
    )
    return header + len(packed_payload).to_bytes(4, "big", signed=False) + packed_payload


def _to_volc_audio_format(audio_format: str) -> str:
    if audio_format == "pcm16":
        return "pcm"
    if audio_format == "webm-opus":
        raise AsrProviderError(
            "unsupported_audio_format",
            "当前语音输入只支持 PCM 录音格式",
            detail="webm-opus is not supported by current Volc adapter",
        )
    raise AsrProviderError(
        "unsupported_audio_format",
        "当前语音输入音频格式不支持",
        detail=audio_format,
    )
