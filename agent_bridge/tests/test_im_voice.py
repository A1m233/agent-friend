"""QQ voice-message inbound resolver tests."""

from __future__ import annotations

from typing import Any

import pytest
from agent_bridge.protocols.im.dedupe import MessageDedupeKey, RecentMessageDeduper
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.voice import (
    VOICE_TRANSCRIPTION_FAILURE_TEXT,
    QQVoiceMessageResolver,
    SafeAudioDownloader,
    VoiceAudioDownloadError,
)
from agent_bridge.protocols.im.voice_bridge_client import (
    VoiceBridgeTranscription,
    VoiceBridgeTranscriptionError,
)
from qqbot_agent_sdk.dto import MessageAttachment, MsgElement


class FakeDownloader:
    def __init__(self, payload: bytes = b"wav-bytes") -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def download(self, url: str, *, max_bytes: int, trace_id: str) -> bytes:
        self.calls.append({"url": url, "max_bytes": max_bytes, "trace_id": trace_id})
        return self.payload


class FakeTranscriber:
    def __init__(self, text: str = "fallback transcript", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def transcribe_wav(
        self,
        audio: bytes,
        *,
        trace_id: str,
        locale: str | None = None,
    ) -> VoiceBridgeTranscription:
        self.calls.append({"audio": audio, "trace_id": trace_id, "locale": locale})
        if self.error is not None:
            raise self.error
        return VoiceBridgeTranscription(text=self.text, trace_id=trace_id)


def make_event(
    *,
    content: str = "",
    attachments: list[MessageAttachment] | None = None,
    msg_elements: list[MsgElement] | None = None,
    message_id: str = "MSG-VOICE-1",
) -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="OPENID-USER",
        user_id="OPENID-USER",
        chat_scope="c2c",
        content=content,
        message_id=message_id,
        timestamp="2026-06-29T10:00:00Z",
        message_type=0,
        attachments=attachments or [],
        msg_elements=msg_elements or [],
    )


def make_resolver(
    *,
    downloader: FakeDownloader | None = None,
    transcriber: FakeTranscriber | None = None,
) -> QQVoiceMessageResolver:
    return QQVoiceMessageResolver(
        downloader=downloader or FakeDownloader(),
        transcriber=transcriber or FakeTranscriber(),
        max_audio_bytes=1024,
    )


@pytest.mark.asyncio
async def test_resolver_uses_qq_asr_refer_text_first() -> None:
    downloader = FakeDownloader()
    transcriber = FakeTranscriber()
    resolver = make_resolver(downloader=downloader, transcriber=transcriber)
    event = make_event(
        attachments=[MessageAttachment(content_type="voice", asr_refer_text="今天去吃饭吗")]
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.event is not None
    assert result.event.content == "今天去吃饭吗"
    assert downloader.calls == []
    assert transcriber.calls == []


@pytest.mark.asyncio
async def test_resolver_uses_msg_element_voice_attachments() -> None:
    resolver = make_resolver()
    event = make_event(
        msg_elements=[
            MsgElement(
                attachments=[
                    MessageAttachment(
                        content_type="voice",
                        asr_refer_text="我现在正在用语音跟你说话，你能听得到吗",
                    )
                ]
            )
        ],
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.event is not None
    assert result.event.content == "我现在正在用语音跟你说话，你能听得到吗"


@pytest.mark.asyncio
async def test_resolver_falls_back_to_voice_bridge() -> None:
    downloader = FakeDownloader(payload=b"wav")
    transcriber = FakeTranscriber(text="fallback text")
    resolver = make_resolver(downloader=downloader, transcriber=transcriber)
    event = make_event(
        attachments=[
            MessageAttachment(
                content_type="audio/wav",
                voice_wav_url="https://media.example.test/voice.wav",
            )
        ]
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.event is not None
    assert result.event.content == "fallback text"
    assert downloader.calls[0]["url"] == "https://media.example.test/voice.wav"
    assert downloader.calls[0]["max_bytes"] == 1024
    assert transcriber.calls[0]["audio"] == b"wav"
    assert transcriber.calls[0]["locale"] == "zh-CN"


@pytest.mark.asyncio
async def test_resolver_combines_text_and_voice_transcript() -> None:
    resolver = make_resolver()
    event = make_event(
        content="这条先按紧急处理",
        attachments=[MessageAttachment(content_type="voice", asr_refer_text="明天上午提醒我")],
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.event is not None
    assert result.event.content == "这条先按紧急处理\n\n[语音转写] 明天上午提醒我"


@pytest.mark.asyncio
async def test_resolver_replies_when_voice_only_transcription_fails() -> None:
    transcriber = FakeTranscriber(
        error=VoiceBridgeTranscriptionError("asr_unavailable", "ASR down")
    )
    resolver = make_resolver(transcriber=transcriber)
    event = make_event(
        attachments=[MessageAttachment(content_type="voice", voice_wav_url="https://m.test/a.wav")]
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "reply"
    assert result.reply is not None
    assert result.reply.text == VOICE_TRANSCRIPTION_FAILURE_TEXT
    assert result.reply.chat_id == "OPENID-USER"
    assert result.reply.reply_to_message_id == "MSG-VOICE-1"


@pytest.mark.asyncio
async def test_resolver_keeps_original_text_when_mixed_voice_fails() -> None:
    transcriber = FakeTranscriber(
        error=VoiceBridgeTranscriptionError("asr_unavailable", "ASR down")
    )
    resolver = make_resolver(transcriber=transcriber)
    event = make_event(
        content="先看这句文字",
        attachments=[MessageAttachment(content_type="voice", voice_wav_url="https://m.test/a.wav")],
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.event is event
    assert result.event.content == "先看这句文字"


@pytest.mark.asyncio
async def test_resolver_noops_for_plain_text() -> None:
    resolver = make_resolver()
    event = make_event(content="纯文字")

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.event is event


@pytest.mark.asyncio
async def test_safe_downloader_rejects_local_urls() -> None:
    downloader = SafeAudioDownloader()

    with pytest.raises(VoiceAudioDownloadError) as exc:
        await downloader.download("http://127.0.0.1/private.wav", max_bytes=1024, trace_id="t")

    assert exc.value.code == "unsafe_url"


def test_recent_message_deduper_ttl() -> None:
    now = 100.0

    def clock() -> float:
        return now

    deduper = RecentMessageDeduper(ttl_seconds=10, max_items=10, clock=clock)
    key = MessageDedupeKey("qq", "c2c", "OPENID", "MSG-1")

    assert deduper.seen_or_mark(key) is False
    assert deduper.seen_or_mark(key) is True

    now = 111.0
    assert deduper.seen_or_mark(key) is False
