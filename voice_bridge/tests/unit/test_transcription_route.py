"""Chat composer transcription WebSocket route tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from voice_bridge.app import create_app_with_runtime
from voice_bridge.asr import (
    AsrPrewarmOptions,
    AsrPrewarmResult,
    AsrProviderUnavailableError,
    AsrSession,
    AsrStartOptions,
    AsrTranscriptEvent,
    DisabledAsrProvider,
)
from voice_bridge.assembly import VoiceBridgeRuntime
from voice_bridge.calls import CallRegistry
from voice_bridge.clients import AgentBridgeClient
from voice_bridge.rtc import VolcRtcClient
from voice_bridge.settings import VoiceBridgeSettings


class FakeAsrSession:
    def __init__(self) -> None:
        self.audio_chunks: list[bytes] = []
        self.cancelled = False
        self.finished = False
        self._events: asyncio.Queue[AsrTranscriptEvent | None] = asyncio.Queue()

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)
        if len(self.audio_chunks) == 1:
            await self._events.put(AsrTranscriptEvent(type="partial", text="你好"))

    async def finish(self) -> None:
        self.finished = True
        await self._events.put(AsrTranscriptEvent(type="final", text="你好世界"))
        await self._events.put(None)

    async def cancel(self) -> None:
        self.cancelled = True
        await self._events.put(None)

    async def events(self) -> AsyncIterator[AsrTranscriptEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                break
            yield event


class FakeAsrProvider:
    def __init__(self) -> None:
        self.session = FakeAsrSession()
        self.options: AsrStartOptions | None = None
        self.prewarm_options: AsrPrewarmOptions | None = None

    async def start(self, options: AsrStartOptions) -> AsrSession:
        self.options = options
        return self.session

    async def prewarm(self, options: AsrPrewarmOptions) -> AsrPrewarmResult:
        self.prewarm_options = options
        return AsrPrewarmResult(status="started", trace_id=options.trace_id, ttl_ms=30_000)


class SlowAsrProvider(FakeAsrProvider):
    async def start(self, options: AsrStartOptions) -> AsrSession:
        await asyncio.sleep(0.05)
        return await super().start(options)


class FailingAsrProvider:
    async def start(self, options: AsrStartOptions) -> AsrSession:
        raise AsrProviderUnavailableError(detail=f"not configured: {options.trace_id}")


@pytest.fixture
def settings() -> VoiceBridgeSettings:
    return VoiceBridgeSettings(
        public_url="https://test.example.com",
        agent_bridge_url="http://test-agent-bridge",
        volc_access_key="AKLT-test",
        volc_secret_key="test-secret",
        volc_rtc_app_id="rtc-app-id",
        volc_rtc_app_key="rtc-app-key",
        volc_speech_app_id="speech-app-id",
        volc_speech_access_token="speech-token",
    )


def build_client(settings: VoiceBridgeSettings, asr_provider: object) -> TestClient:
    runtime = VoiceBridgeRuntime(
        settings=settings,
        rtc_client=VolcRtcClient(
            access_key=settings.volc_access_key,
            secret_key=settings.volc_secret_key,
        ),
        call_registry=CallRegistry(),
        agent_bridge=AgentBridgeClient(settings.agent_bridge_url),
        asr_provider=asr_provider,  # type: ignore[arg-type]
    )
    return TestClient(create_app_with_runtime(runtime))


def test_transcription_stream_sends_partial_final_and_stopped(
    settings: VoiceBridgeSettings,
) -> None:
    provider = FakeAsrProvider()
    client = build_client(settings, provider)

    with client.websocket_connect("/voice/transcriptions/stream?trace_id=trace-1") as ws:
        ws.send_json(
            {
                "type": "start",
                "traceId": "trace-1",
                "audio": {"format": "pcm16", "sampleRate": 16000, "channels": 1},
                "locale": "zh-CN",
            }
        )
        assert ws.receive_json() == {"type": "ready", "traceId": "trace-1"}
        ws.send_bytes(b"audio-1")
        assert ws.receive_json() == {
            "type": "partial",
            "traceId": "trace-1",
            "text": "你好",
        }
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {
            "type": "final",
            "traceId": "trace-1",
            "text": "你好世界",
        }
        assert ws.receive_json() == {
            "type": "stopped",
            "traceId": "trace-1",
            "reason": "client_stop",
        }

    assert provider.options is not None
    assert provider.options.audio_format == "pcm16"
    assert provider.session.audio_chunks == [b"audio-1"]
    assert provider.session.finished


def test_transcription_prewarm_endpoint(settings: VoiceBridgeSettings) -> None:
    provider = FakeAsrProvider()
    client = build_client(settings, provider)

    response = client.post(
        "/voice/transcriptions/prewarm",
        json={"traceId": "warm-route", "reason": "composer_active"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "started",
        "traceId": "warm-route",
        "ttlMs": 30_000,
    }
    assert provider.prewarm_options == AsrPrewarmOptions(
        trace_id="warm-route",
        reason="composer_active",
    )


def test_transcription_health_endpoint(settings: VoiceBridgeSettings) -> None:
    provider = FakeAsrProvider()
    client = build_client(settings, provider)

    response = client.get("/voice/transcriptions/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stream": True, "prewarm": True}


def test_transcription_stream_queues_audio_before_provider_ready(
    settings: VoiceBridgeSettings,
) -> None:
    provider = SlowAsrProvider()
    client = build_client(settings, provider)

    with client.websocket_connect("/voice/transcriptions/stream?trace_id=trace-early") as ws:
        ws.send_json(
            {
                "type": "start",
                "traceId": "trace-early",
                "audio": {"format": "pcm16", "sampleRate": 16000, "channels": 1},
                "locale": "zh-CN",
            }
        )
        ws.send_bytes(b"audio-before-ready")
        assert ws.receive_json() == {"type": "ready", "traceId": "trace-early"}
        assert ws.receive_json() == {
            "type": "partial",
            "traceId": "trace-early",
            "text": "你好",
        }
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "final"
        assert ws.receive_json() == {
            "type": "stopped",
            "traceId": "trace-early",
            "reason": "client_stop",
        }

    assert provider.session.audio_chunks == [b"audio-before-ready"]


def test_transcription_stream_cancel_calls_provider_cancel(settings: VoiceBridgeSettings) -> None:
    provider = FakeAsrProvider()
    client = build_client(settings, provider)

    with client.websocket_connect("/voice/transcriptions/stream") as ws:
        ws.send_json(
            {
                "type": "start",
                "traceId": "trace-cancel",
                "audio": {"format": "pcm16", "sampleRate": 16000, "channels": 1},
            }
        )
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {
            "type": "stopped",
            "traceId": "trace-cancel",
            "reason": "client_cancel",
        }

    assert provider.session.cancelled


def test_transcription_stream_returns_provider_error(settings: VoiceBridgeSettings) -> None:
    client = build_client(settings, FailingAsrProvider())

    with client.websocket_connect("/voice/transcriptions/stream") as ws:
        ws.send_json(
            {
                "type": "start",
                "traceId": "trace-error",
                "audio": {"format": "pcm16", "sampleRate": 16000, "channels": 1},
            }
        )
        assert ws.receive_json() == {
            "type": "error",
            "traceId": "trace-error",
            "code": "asr_provider_unavailable",
            "message": "语音输入识别服务还没有接上，请稍后再试",
        }


def test_disabled_provider_has_stable_user_message() -> None:
    with pytest.raises(AsrProviderUnavailableError):
        asyncio.run(
            DisabledAsrProvider().start(
                AsrStartOptions(
                    trace_id="trace-disabled",
                    audio_format="pcm16",
                    sample_rate=16000,
                    channels=1,
                )
            )
        )
