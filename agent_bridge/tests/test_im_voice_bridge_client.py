"""voice_bridge one-shot transcription client tests."""

from __future__ import annotations

import httpx
import pytest
import respx
from agent_bridge.protocols.im.voice_bridge_client import (
    VoiceBridgeTranscriptionClient,
    VoiceBridgeTranscriptionError,
)


@pytest.mark.asyncio
@respx.mock
async def test_voice_bridge_client_posts_wav_and_returns_text(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post("http://voice.test/voice/transcriptions/audio").mock(
        return_value=httpx.Response(
            200,
            json={"traceId": "trace-1", "text": "你好", "source": "voice_bridge"},
        )
    )
    client = VoiceBridgeTranscriptionClient("http://voice.test")

    result = await client.transcribe_wav(b"wav", trace_id="trace-1", locale="zh-CN")

    assert result.text == "你好"
    assert result.trace_id == "trace-1"
    assert result.source == "voice_bridge"
    assert route.called
    request = route.calls.last.request
    assert request.url.params["trace_id"] == "trace-1"
    assert request.url.params["format"] == "wav"
    assert request.url.params["locale"] == "zh-CN"
    assert request.content == b"wav"


@pytest.mark.asyncio
@respx.mock
async def test_voice_bridge_client_maps_error_payload(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("http://voice.test/voice/transcriptions/audio").mock(
        return_value=httpx.Response(
            503,
            json={"traceId": "trace-2", "code": "asr_unavailable", "message": "down"},
        )
    )
    client = VoiceBridgeTranscriptionClient("http://voice.test")

    with pytest.raises(VoiceBridgeTranscriptionError) as exc:
        await client.transcribe_wav(b"wav", trace_id="trace-2")

    assert exc.value.code == "asr_unavailable"
    assert exc.value.message == "down"
