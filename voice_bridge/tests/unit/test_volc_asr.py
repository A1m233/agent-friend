"""Volc streaming ASR adapter unit tests."""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any

import pytest
from voice_bridge.asr import AsrPrewarmOptions, AsrStartOptions, AsrTranscriptEvent
from voice_bridge.asr.volc import (
    VolcAsrProvider,
    build_audio_request,
    build_full_client_request,
    build_volc_headers,
    extract_transcript_text,
    parse_volc_frame,
)
from voice_bridge.settings import VoiceBridgeSettings


class FakeVolcWebSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self.to_receive: asyncio.Queue[bytes] = asyncio.Queue()

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes:
        return await self.to_receive.get()

    async def close(self) -> None:
        self.closed = True


def make_options() -> AsrStartOptions:
    return AsrStartOptions(
        trace_id="trace-volc",
        audio_format="pcm16",
        sample_rate=16000,
        channels=1,
        locale="zh-CN",
    )


def make_server_response(text: str, *, is_last: bool = False) -> bytes:
    payload = gzip.compress(
        json.dumps({"result": {"text": text}}, ensure_ascii=False).encode("utf-8")
    )
    flags = 0b0011 if is_last else 0b0001
    header = bytes([0x11, 0x90 | flags, 0x11, 0x00])
    sequence = (-1 if is_last else 1).to_bytes(4, "big", signed=True)
    return header + sequence + len(payload).to_bytes(4, "big") + payload


def test_build_volc_headers_uses_existing_old_console_credentials() -> None:
    settings = VoiceBridgeSettings(
        volc_speech_app_id="speech-app",
        volc_speech_access_token="speech-token",
    )

    headers = build_volc_headers(settings, "trace-1")

    assert headers["X-Api-App-Key"] == "speech-app"
    assert headers["X-Api-Access-Key"] == "speech-token"
    assert headers["X-Api-Resource-Id"] == "volc.bigasr.sauc.duration"
    assert headers["X-Api-Request-Id"] == "trace-1"
    assert headers["X-Api-Connect-Id"] == "trace-1"
    assert headers["X-Api-Sequence"] == "-1"


def test_build_volc_headers_prefers_new_console_api_key() -> None:
    settings = VoiceBridgeSettings(
        volc_speech_api_key="api-key",
        volc_speech_app_id="speech-app",
        volc_speech_access_token="speech-token",
        volc_speech_resource_id="volc.seedasr.sauc.duration",
    )

    headers = build_volc_headers(settings, "trace-2")

    assert headers["X-Api-Key"] == "api-key"
    assert "X-Api-App-Key" not in headers
    assert headers["X-Api-Resource-Id"] == "volc.seedasr.sauc.duration"


def test_full_client_request_maps_frontend_pcm_to_volc_pcm() -> None:
    frame = build_full_client_request(make_options())
    parsed = parse_volc_frame(frame)

    assert parsed.payload["audio"]["format"] == "pcm"
    assert parsed.payload["audio"]["rate"] == 16000
    assert parsed.payload["audio"]["bits"] == 16
    assert parsed.payload["audio"]["channel"] == 1
    assert parsed.payload["audio"]["language"] == "zh-CN"
    assert parsed.payload["request"]["model_name"] == "bigmodel"


def test_audio_request_marks_last_flag() -> None:
    normal = parse_volc_frame(build_audio_request(b"abc", is_last=False))
    last = parse_volc_frame(build_audio_request(b"abc", is_last=True))

    assert not normal.is_last
    assert last.is_last
    assert normal.payload == b"abc"
    assert last.payload == b"abc"


def test_extract_transcript_text_accepts_dict_and_list_payloads() -> None:
    assert extract_transcript_text({"result": {"text": "你好"}}) == "你好"
    assert extract_transcript_text({"result": [{"text": "你"}, {"text": "好"}]}) == "你好"
    assert extract_transcript_text({"result": None}) == ""


@pytest.mark.asyncio
async def test_provider_session_streams_partial_and_final() -> None:
    fake_ws = FakeVolcWebSocket()
    captured: dict[str, Any] = {}

    async def fake_connect(uri: str, **kwargs: Any) -> FakeVolcWebSocket:
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return fake_ws

    settings = VoiceBridgeSettings(
        volc_speech_app_id="speech-app",
        volc_speech_access_token="speech-token",
    )
    provider = VolcAsrProvider(settings, connect_factory=fake_connect)
    session = await provider.start(make_options())

    assert captured["uri"] == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    assert captured["kwargs"]["compression"] is None
    assert len(fake_ws.sent) == 1

    await session.send_audio(b"chunk-1")
    await session.send_audio(b"chunk-2")
    await session.finish()
    assert len(fake_ws.sent) == 3
    assert parse_volc_frame(fake_ws.sent[1]).payload == b"chunk-1"
    final_audio = parse_volc_frame(fake_ws.sent[2])
    assert final_audio.payload == b"chunk-2"
    assert final_audio.is_last

    await fake_ws.to_receive.put(make_server_response("你", is_last=False))
    await fake_ws.to_receive.put(make_server_response("你好", is_last=True))

    events: list[AsrTranscriptEvent] = []
    async for event in session.events():
        events.append(event)

    assert events == [
        AsrTranscriptEvent(type="partial", text="你"),
        AsrTranscriptEvent(type="final", text="你好"),
    ]
    assert fake_ws.closed


@pytest.mark.asyncio
async def test_provider_prewarm_connects_without_starting_recognition() -> None:
    sockets: list[FakeVolcWebSocket] = []

    async def fake_connect(uri: str, **kwargs: Any) -> FakeVolcWebSocket:
        ws = FakeVolcWebSocket()
        sockets.append(ws)
        return ws

    settings = VoiceBridgeSettings(
        volc_speech_app_id="speech-app",
        volc_speech_access_token="speech-token",
    )
    provider = VolcAsrProvider(settings, connect_factory=fake_connect)

    result = await provider.prewarm(AsrPrewarmOptions(trace_id="warm-1", reason="unit"))

    assert result.status == "started"
    assert len(sockets) == 1
    assert sockets[0].sent == []

    session = await provider.start(make_options())

    assert len(sockets) == 1
    assert len(sockets[0].sent) == 1
    await session.cancel()


@pytest.mark.asyncio
async def test_provider_prewarm_reuses_existing_warm_socket() -> None:
    sockets: list[FakeVolcWebSocket] = []

    async def fake_connect(uri: str, **kwargs: Any) -> FakeVolcWebSocket:
        ws = FakeVolcWebSocket()
        sockets.append(ws)
        return ws

    settings = VoiceBridgeSettings(
        volc_speech_app_id="speech-app",
        volc_speech_access_token="speech-token",
    )
    provider = VolcAsrProvider(settings, connect_factory=fake_connect)

    first = await provider.prewarm(AsrPrewarmOptions(trace_id="warm-1", reason="unit"))
    second = await provider.prewarm(AsrPrewarmOptions(trace_id="warm-2", reason="renew"))

    assert first.status == "started"
    assert second.status == "already_warm"
    assert len(sockets) == 1

    session = await provider.start(make_options())
    assert len(sockets) == 1
    assert len(sockets[0].sent) == 1
    await session.cancel()


@pytest.mark.asyncio
async def test_provider_prewarm_can_be_disabled() -> None:
    async def fake_connect(uri: str, **kwargs: Any) -> FakeVolcWebSocket:
        raise AssertionError("prewarm should not connect when disabled")

    settings = VoiceBridgeSettings(
        volc_speech_app_id="speech-app",
        volc_speech_access_token="speech-token",
        voice_input_prewarm_enabled=False,
    )
    provider = VolcAsrProvider(settings, connect_factory=fake_connect)

    result = await provider.prewarm(AsrPrewarmOptions(trace_id="warm-disabled", reason="unit"))

    assert result.status == "disabled"
