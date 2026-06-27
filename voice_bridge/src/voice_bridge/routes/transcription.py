"""Chat composer voice-input transcription routes."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.websockets import WebSocketState

from ..asr import (
    AsrPrewarmOptions,
    AsrPrewarmResult,
    AsrProviderError,
    AsrProviderUnavailableError,
    AsrSession,
    AsrStartOptions,
)
from ..assembly import VoiceBridgeRuntime
from ..latency import log_latency, monotonic_ms

logger = logging.getLogger(__name__)

_PROVIDER_DRAIN_TIMEOUT_SEC = 8.0
_PRE_READY_AUDIO_QUEUE_MAX_BYTES = 8 * 1024 * 1024


class InvalidTranscriptionRequest(Exception):
    """Client sent an invalid transcription WebSocket frame."""


class TranscriptionAudioOptions(BaseModel):
    """Audio format sent by the frontend."""

    model_config = ConfigDict(populate_by_name=True)

    format: Literal["pcm16", "webm-opus"]
    sample_rate: int = Field(alias="sampleRate", ge=8000, le=48000)
    channels: int = Field(ge=1, le=2)


class TranscriptionStartEvent(BaseModel):
    """Client ``start`` control frame."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["start"]
    trace_id: str = Field(alias="traceId")
    audio: TranscriptionAudioOptions
    locale: str | None = None


class TranscriptionControlEvent(BaseModel):
    """Client control frame after ``start``."""

    type: Literal["stop", "cancel"]


class TranscriptionPrewarmRequest(BaseModel):
    """Request body for connect-only ASR warmup."""

    model_config = ConfigDict(populate_by_name=True)

    trace_id: str | None = Field(default=None, alias="traceId")
    reason: str = "composer_active"


def register_transcription_routes(app: FastAPI, runtime: VoiceBridgeRuntime) -> None:
    """Register chat composer transcription routes."""

    router = APIRouter(prefix="/voice", tags=["voice-transcription"])

    @router.get("/transcriptions/healthz")
    def transcription_healthz() -> dict[str, Any]:
        provider = runtime.asr_provider
        return {
            "status": "ok",
            "stream": provider is not None,
            "prewarm": provider is not None and callable(getattr(provider, "prewarm", None)),
        }

    @router.post("/transcriptions/prewarm")
    async def transcription_prewarm(request: TranscriptionPrewarmRequest) -> dict[str, Any]:
        trace_id = request.trace_id or f"voice-input-prewarm-{uuid4()}"
        started_ms = monotonic_ms()
        provider = runtime.asr_provider
        prewarm = getattr(provider, "prewarm", None) if provider is not None else None

        log_latency(
            logger,
            "voice_input_prewarm_request",
            trace_id=trace_id,
            reason=request.reason,
        )

        if prewarm is None:
            result = AsrPrewarmResult(
                status="unavailable",
                trace_id=trace_id,
                ttl_ms=0,
                message="ASR provider does not support prewarm",
            )
        else:
            try:
                result = await prewarm(AsrPrewarmOptions(trace_id=trace_id, reason=request.reason))
            except AsrProviderUnavailableError as e:
                result = AsrPrewarmResult(
                    status="unavailable",
                    trace_id=trace_id,
                    ttl_ms=0,
                    message=e.detail or e.message,
                )
            except Exception as e:
                logger.warning("voice input prewarm route failed trace_id=%s: %s", trace_id, e)
                result = AsrPrewarmResult(
                    status="error",
                    trace_id=trace_id,
                    ttl_ms=0,
                    message=str(e),
                )

        log_latency(
            logger,
            "voice_input_prewarm_response",
            trace_id=trace_id,
            reason=request.reason,
            status=result.status,
            warm_age_ms=result.warm_age_ms,
            ttl_ms=result.ttl_ms,
            elapsed_ms=monotonic_ms() - started_ms,
        )
        return _prewarm_result_payload(result)

    @router.websocket("/transcriptions/stream")
    async def transcription_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        query_trace_id = websocket.query_params.get("trace_id") or ""
        start_ms = monotonic_ms()
        session: AsrSession | None = None
        session_start_task: asyncio.Task[AsrSession] | None = None
        provider_task: asyncio.Task[None] | None = None
        receive_task: asyncio.Task[Any] | None = None
        send_lock = asyncio.Lock()
        trace_id = query_trace_id or "unknown"
        stop_reason: Literal["client_stop", "client_cancel", "provider_done"] = "provider_done"
        provider_failed = False

        async def send_event(payload: dict[str, Any]) -> None:
            if websocket.application_state != WebSocketState.CONNECTED:
                return
            async with send_lock:
                await websocket.send_json(payload)

        async def send_error(code: str, message: str) -> None:
            await send_event(
                {"type": "error", "traceId": trace_id, "code": code, "message": message}
            )

        log_latency(
            logger,
            "voice_input_ws_connected",
            trace_id=query_trace_id or None,
        )

        try:
            start_event = await _receive_start(websocket, query_trace_id=query_trace_id)
            trace_id = start_event.trace_id
            provider = runtime.asr_provider
            if provider is None:
                raise AsrProviderUnavailableError(detail="runtime.asr_provider is None")

            log_latency(
                logger,
                "voice_input_start",
                trace_id=trace_id,
                format=start_event.audio.format,
                sample_rate=start_event.audio.sample_rate,
                channels=start_event.audio.channels,
            )

            first_audio_seen = False
            first_partial_seen = False
            pending_audio: list[bytes] = []
            pending_audio_bytes = 0
            pending_audio_overflow_logged = False
            pre_ready_stop = False

            def queue_pre_ready_audio(chunk: bytes) -> None:
                nonlocal pending_audio_bytes, pending_audio_overflow_logged
                pending_audio.append(chunk)
                pending_audio_bytes += len(chunk)
                while pending_audio_bytes > _PRE_READY_AUDIO_QUEUE_MAX_BYTES and pending_audio:
                    dropped = pending_audio.pop(0)
                    pending_audio_bytes -= len(dropped)
                    if not pending_audio_overflow_logged:
                        pending_audio_overflow_logged = True
                        log_latency(
                            logger,
                            "voice_input_pre_ready_audio_dropped",
                            trace_id=trace_id,
                            dropped_bytes=len(dropped),
                            queued_bytes=pending_audio_bytes,
                        )

            async def pump_provider_events() -> None:
                nonlocal first_partial_seen, provider_failed
                try:
                    assert session is not None
                    async for event in session.events():
                        if event.type == "partial":
                            if not first_partial_seen:
                                first_partial_seen = True
                                log_latency(
                                    logger,
                                    "voice_input_first_partial",
                                    trace_id=trace_id,
                                    elapsed_ms=monotonic_ms() - start_ms,
                                    text_len=len(event.text),
                                )
                            await send_event(
                                {
                                    "type": "partial",
                                    "traceId": trace_id,
                                    "text": event.text,
                                }
                            )
                        elif event.type == "final":
                            log_latency(
                                logger,
                                "voice_input_final",
                                trace_id=trace_id,
                                elapsed_ms=monotonic_ms() - start_ms,
                                text_len=len(event.text),
                            )
                            await send_event(
                                {
                                    "type": "final",
                                    "traceId": trace_id,
                                    "text": event.text,
                                }
                            )
                except AsrProviderError as e:
                    provider_failed = True
                    log_latency(
                        logger,
                        "voice_input_error",
                        trace_id=trace_id,
                        code=e.code,
                        elapsed_ms=monotonic_ms() - start_ms,
                    )
                    logger.warning(
                        "voice input provider error trace_id=%s code=%s", trace_id, e.code
                    )
                    await send_error(e.code, e.message)
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover - defensive safety net.
                    provider_failed = True
                    log_latency(
                        logger,
                        "voice_input_error",
                        trace_id=trace_id,
                        code="asr_provider_error",
                        elapsed_ms=monotonic_ms() - start_ms,
                    )
                    logger.exception("voice input provider crashed trace_id=%s", trace_id)
                    await send_error("asr_provider_error", "语音输入识别暂时失败，请稍后再试")

            session_start_task = asyncio.create_task(
                provider.start(
                    AsrStartOptions(
                        trace_id=trace_id,
                        audio_format=start_event.audio.format,
                        sample_rate=start_event.audio.sample_rate,
                        channels=start_event.audio.channels,
                        locale=start_event.locale,
                    )
                )
            )
            receive_task = asyncio.create_task(websocket.receive())

            while session is None:
                assert session_start_task is not None
                wait_tasks: set[asyncio.Task[Any]] = {session_start_task}
                if receive_task is not None:
                    wait_tasks.add(receive_task)
                done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)

                if session_start_task in done:
                    session = session_start_task.result()
                    session_start_task = None
                    break

                if receive_task is None or receive_task not in done:
                    continue

                message = receive_task.result()
                receive_task = None

                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(code=message.get("code", 1000))

                if chunk := message.get("bytes"):
                    if not first_audio_seen:
                        first_audio_seen = True
                        log_latency(
                            logger,
                            "voice_input_first_audio",
                            trace_id=trace_id,
                            elapsed_ms=monotonic_ms() - start_ms,
                            bytes_len=len(chunk),
                        )
                    queue_pre_ready_audio(chunk)
                elif text := message.get("text"):
                    control = _parse_control(text)
                    if control.type == "cancel":
                        stop_reason = "client_cancel"
                        session_start_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await session_start_task
                        session_start_task = None
                        log_latency(
                            logger,
                            "voice_input_stop",
                            trace_id=trace_id,
                            reason=stop_reason,
                            elapsed_ms=monotonic_ms() - start_ms,
                        )
                        await send_event(
                            {"type": "stopped", "traceId": trace_id, "reason": stop_reason}
                        )
                        return
                    if control.type == "stop":
                        stop_reason = "client_stop"
                        pre_ready_stop = True

                if not pre_ready_stop:
                    receive_task = asyncio.create_task(websocket.receive())

            log_latency(
                logger,
                "voice_input_ready",
                trace_id=trace_id,
                elapsed_ms=monotonic_ms() - start_ms,
                queued_chunks=len(pending_audio),
                queued_bytes=pending_audio_bytes,
            )
            await send_event({"type": "ready", "traceId": trace_id})

            provider_task = asyncio.create_task(pump_provider_events())

            for chunk in pending_audio:
                await session.send_audio(chunk)
            if pending_audio:
                log_latency(
                    logger,
                    "voice_input_pre_ready_audio_flushed",
                    trace_id=trace_id,
                    elapsed_ms=monotonic_ms() - start_ms,
                    queued_chunks=len(pending_audio),
                    queued_bytes=pending_audio_bytes,
                )
            pending_audio = []
            pending_audio_bytes = 0

            if pre_ready_stop:
                await session.finish()
            else:
                if receive_task is None:
                    receive_task = asyncio.create_task(websocket.receive())

                while True:
                    assert provider_task is not None
                    assert receive_task is not None
                    done, _ = await asyncio.wait(
                        {provider_task, receive_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if provider_task in done:
                        break

                    message = receive_task.result()
                    receive_task = None

                    if message["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(code=message.get("code", 1000))

                    if chunk := message.get("bytes"):
                        if not first_audio_seen:
                            first_audio_seen = True
                            log_latency(
                                logger,
                                "voice_input_first_audio",
                                trace_id=trace_id,
                                elapsed_ms=monotonic_ms() - start_ms,
                                bytes_len=len(chunk),
                            )
                        await session.send_audio(chunk)
                    elif text := message.get("text"):
                        control = _parse_control(text)
                        if control.type == "stop":
                            stop_reason = "client_stop"
                            await session.finish()
                            break
                        if control.type == "cancel":
                            stop_reason = "client_cancel"
                            await session.cancel()
                            break

                    receive_task = asyncio.create_task(websocket.receive())

            if receive_task is not None:
                receive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await receive_task
                receive_task = None

            if provider_task is not None and not provider_task.done():
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(provider_task, timeout=_PROVIDER_DRAIN_TIMEOUT_SEC)
                if not provider_task.done():
                    provider_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await provider_task
            elif provider_task is not None:
                await provider_task

            if not provider_failed:
                log_latency(
                    logger,
                    "voice_input_stop",
                    trace_id=trace_id,
                    reason=stop_reason,
                    elapsed_ms=monotonic_ms() - start_ms,
                )
                await send_event({"type": "stopped", "traceId": trace_id, "reason": stop_reason})
        except WebSocketDisconnect:
            log_latency(
                logger,
                "voice_input_stop",
                trace_id=trace_id,
                reason="client_disconnect",
                elapsed_ms=monotonic_ms() - start_ms,
            )
            if session is not None:
                await session.cancel()
        except AsrProviderError as e:
            log_latency(
                logger,
                "voice_input_error",
                trace_id=trace_id,
                code=e.code,
                elapsed_ms=monotonic_ms() - start_ms,
            )
            logger.warning("voice input start failed trace_id=%s code=%s", trace_id, e.code)
            await send_error(e.code, e.message)
        except (InvalidTranscriptionRequest, ValidationError) as e:
            log_latency(
                logger,
                "voice_input_error",
                trace_id=trace_id,
                code="invalid_request",
                elapsed_ms=monotonic_ms() - start_ms,
            )
            logger.warning("voice input invalid request trace_id=%s error=%s", trace_id, e)
            await send_error("invalid_request", "语音输入请求参数有误")
        except json.JSONDecodeError:
            log_latency(
                logger,
                "voice_input_error",
                trace_id=trace_id,
                code="invalid_json",
                elapsed_ms=monotonic_ms() - start_ms,
            )
            await send_error("invalid_json", "语音输入请求格式有误")
        finally:
            if session_start_task is not None and not session_start_task.done():
                session_start_task.cancel()
                with suppress(asyncio.CancelledError):
                    await session_start_task
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await receive_task
            if provider_task is not None and not provider_task.done():
                provider_task.cancel()
                with suppress(asyncio.CancelledError):
                    await provider_task
            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close()

    app.include_router(router)


async def _receive_start(websocket: WebSocket, *, query_trace_id: str) -> TranscriptionStartEvent:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(code=message.get("code", 1000))
    text = message.get("text")
    if not text:
        raise InvalidTranscriptionRequest("first frame must be start JSON")
    raw = json.loads(text)
    if query_trace_id and not raw.get("traceId") and not raw.get("trace_id"):
        raw["traceId"] = query_trace_id
    return TranscriptionStartEvent.model_validate(raw)


def _parse_control(text: str) -> TranscriptionControlEvent:
    return TranscriptionControlEvent.model_validate(json.loads(text))


def _prewarm_result_payload(result: AsrPrewarmResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status,
        "traceId": result.trace_id,
        "ttlMs": result.ttl_ms,
    }
    if result.warm_age_ms is not None:
        payload["warmAgeMs"] = result.warm_age_ms
    if result.message:
        payload["message"] = result.message
    return payload
