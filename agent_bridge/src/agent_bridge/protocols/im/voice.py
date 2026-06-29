"""QQ voice-message normalization for IM inbound events."""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from .content import OutboundContent
from .event import InboundEvent
from .inbound_resolver import InboundResolveResult
from .voice_bridge_client import VoiceBridgeTranscription, VoiceBridgeTranscriptionError

_sdk_is_voice_content_type: Callable[[str, str], bool] | None
try:  # Keep the fallback cheap if qqbot-agent-sdk changes optional audio helpers.
    from qqbot_agent_sdk.audio import is_voice_content_type as _sdk_is_voice_content_type
except Exception:  # pragma: no cover - defensive compatibility fallback.
    _sdk_is_voice_content_type = None

logger = logging.getLogger(__name__)

__all__ = [
    "VOICE_TRANSCRIPTION_FAILURE_TEXT",
    "QQVoiceMessageResolver",
    "SafeAudioDownloader",
    "VoiceAudioDownloadError",
]

VOICE_TRANSCRIPTION_FAILURE_TEXT = "这条语音我暂时没听清，可以再发一次或改发文字。"


class VoiceAudioDownloadError(Exception):
    """Raised when a QQ audio attachment cannot be safely downloaded."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(detail or message)
        self.code = code
        self.message = message
        self.detail = detail


class VoiceAudioDownloader(Protocol):
    async def download(self, url: str, *, max_bytes: int, trace_id: str) -> bytes:
        """Download one audio payload."""


class VoiceTranscriber(Protocol):
    async def transcribe_wav(
        self,
        audio: bytes,
        *,
        trace_id: str,
        locale: str | None = None,
    ) -> VoiceBridgeTranscription:
        """Transcribe WAV audio bytes."""


class SafeAudioDownloader:
    """Conservative in-memory downloader for QQ audio attachment URLs."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | float | None = None,
        max_redirects: int = 3,
    ) -> None:
        self._timeout = timeout or httpx.Timeout(connect=2.0, read=20.0, write=10.0, pool=2.0)
        self._max_redirects = max_redirects

    async def download(self, url: str, *, max_bytes: int, trace_id: str) -> bytes:
        current_url = _normalize_attachment_url(url)
        redirects = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                while True:
                    _validate_download_url(current_url)
                    async with client.stream("GET", current_url) as response:
                        if 300 <= response.status_code < 400:
                            if redirects >= self._max_redirects:
                                raise VoiceAudioDownloadError(
                                    "too_many_redirects",
                                    "语音附件下载跳转过多",
                                    detail=f"trace_id={trace_id}",
                                )
                            location = response.headers.get("location")
                            if not location:
                                raise VoiceAudioDownloadError(
                                    "bad_redirect",
                                    "语音附件下载跳转地址无效",
                                    detail=f"trace_id={trace_id}",
                                )
                            current_url = urljoin(current_url, location)
                            redirects += 1
                            continue

                        response.raise_for_status()
                        length = _content_length(response)
                        if length is not None and length > max_bytes:
                            raise VoiceAudioDownloadError(
                                "audio_too_large",
                                "语音附件过大，暂时无法识别",
                                detail=f"content_length={length} trace_id={trace_id}",
                            )

                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise VoiceAudioDownloadError(
                                    "audio_too_large",
                                    "语音附件过大，暂时无法识别",
                                    detail=f"bytes={total} trace_id={trace_id}",
                                )
                            chunks.append(chunk)
                        if not chunks:
                            raise VoiceAudioDownloadError(
                                "empty_audio",
                                "语音附件为空，暂时无法识别",
                                detail=f"trace_id={trace_id}",
                            )
                        return b"".join(chunks)
        except VoiceAudioDownloadError:
            raise
        except httpx.HTTPStatusError as e:
            raise VoiceAudioDownloadError(
                "http_status",
                "语音附件下载失败",
                detail=f"status={e.response.status_code} trace_id={trace_id}",
            ) from e
        except httpx.HTTPError as e:
            raise VoiceAudioDownloadError(
                "network_error",
                "语音附件下载失败",
                detail=f"{type(e).__name__}: {e}",
            ) from e


class QQVoiceMessageResolver:
    """Convert QQ C2C voice attachments into text before ``IMRouter``."""

    def __init__(
        self,
        *,
        downloader: VoiceAudioDownloader,
        transcriber: VoiceTranscriber,
        max_audio_bytes: int,
        max_voice_attachments: int = 1,
        failure_text: str = VOICE_TRANSCRIPTION_FAILURE_TEXT,
    ) -> None:
        self._downloader = downloader
        self._transcriber = transcriber
        self._max_audio_bytes = max_audio_bytes
        self._max_voice_attachments = max_voice_attachments
        self._failure_text = failure_text

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        if im_type != "qq" or event.chat_scope != "c2c":
            return InboundResolveResult.route(event)

        voice_attachments = _voice_attachments_for(event)
        if not voice_attachments:
            return InboundResolveResult.route(event)

        original_text = (event.content or "").strip()
        transcripts: list[str] = []
        for index, attachment in enumerate(voice_attachments[: self._max_voice_attachments]):
            trace_id = _trace_id_for(event, index=index)
            try:
                transcript = await self._transcribe_attachment(attachment, trace_id=trace_id)
            except (VoiceAudioDownloadError, VoiceBridgeTranscriptionError) as e:
                logger.warning(
                    "qq voice transcription failed message_id=%s chat_id=%s stage=%s code=%s",
                    event.message_id,
                    event.chat_id,
                    type(e).__name__,
                    getattr(e, "code", "unknown"),
                )
                continue
            if transcript:
                transcripts.append(transcript)

        if transcripts:
            content = _combine_content(original_text, transcripts)
            logger.info(
                "qq voice transcription resolved message_id=%s chat_id=%s transcripts=%d",
                event.message_id,
                event.chat_id,
                len(transcripts),
            )
            return InboundResolveResult.route(replace(event, content=content))

        if original_text:
            logger.warning(
                "qq voice transcription failed but text content exists message_id=%s chat_id=%s",
                event.message_id,
                event.chat_id,
            )
            return InboundResolveResult.route(event)

        return InboundResolveResult.direct_reply(
            OutboundContent(
                chat_id=event.chat_id,
                chat_scope=event.chat_scope,
                text=self._failure_text,
                reply_to_message_id=event.message_id,
            ),
            reason="voice_transcription_failed",
        )

    async def _transcribe_attachment(self, attachment: object, *, trace_id: str) -> str:
        refer_text = str(getattr(attachment, "asr_refer_text", "") or "").strip()
        if refer_text:
            logger.info(
                "qq voice uses asr_refer_text trace_id=%s text_len=%d", trace_id, len(refer_text)
            )
            return refer_text

        url = _audio_url_for(attachment)
        if not url:
            raise VoiceAudioDownloadError(
                "missing_url",
                "语音附件没有可下载地址",
                detail=f"trace_id={trace_id}",
            )

        audio = await self._downloader.download(
            url,
            max_bytes=self._max_audio_bytes,
            trace_id=trace_id,
        )
        result = await self._transcriber.transcribe_wav(audio, trace_id=trace_id, locale="zh-CN")
        return result.text.strip()


def _is_voice_attachment(attachment: object) -> bool:
    asr_refer_text = str(getattr(attachment, "asr_refer_text", "") or "").strip()
    voice_wav_url = str(getattr(attachment, "voice_wav_url", "") or "").strip()
    if asr_refer_text or voice_wav_url:
        return True
    content_type = str(getattr(attachment, "content_type", "") or "")
    filename = str(getattr(attachment, "filename", "") or "")
    if _sdk_is_voice_content_type is not None:
        return bool(_sdk_is_voice_content_type(content_type, filename))
    return content_type.strip().lower().startswith("audio/")


def _voice_attachments_for(event: InboundEvent) -> list[object]:
    attachments: list[object] = list(event.attachments)
    for element in event.msg_elements:
        attachments.extend(getattr(element, "attachments", []) or [])
    return [att for att in attachments if _is_voice_attachment(att)]


def _audio_url_for(attachment: object) -> str:
    voice_wav_url = str(getattr(attachment, "voice_wav_url", "") or "").strip()
    if voice_wav_url:
        return _normalize_attachment_url(voice_wav_url)
    resolved_url = getattr(attachment, "resolved_url", "")
    if resolved_url:
        return _normalize_attachment_url(str(resolved_url))
    return _normalize_attachment_url(str(getattr(attachment, "url", "") or ""))


def _combine_content(original_text: str, transcripts: list[str]) -> str:
    transcript_text = "\n".join(t.strip() for t in transcripts if t.strip())
    if original_text and transcript_text:
        return f"{original_text}\n\n[语音转写] {transcript_text}"
    return transcript_text or original_text


def _trace_id_for(event: InboundEvent, *, index: int) -> str:
    raw = (event.message_id or "unknown").replace(" ", "_")
    return f"qq-voice-{raw[:80]}-{index}"


def _normalize_attachment_url(url: str) -> str:
    raw = url.strip()
    if raw.startswith("//"):
        return f"https:{raw}"
    return raw


def _validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise VoiceAudioDownloadError(
            "unsafe_url",
            "语音附件下载地址不安全",
            detail=f"unsupported scheme={parsed.scheme!r}",
        )
    host = parsed.hostname
    if not host:
        raise VoiceAudioDownloadError("unsafe_url", "语音附件下载地址无效")
    host_lower = host.lower().rstrip(".")
    if host_lower in {"localhost"} or host_lower.endswith(".local"):
        raise VoiceAudioDownloadError("unsafe_url", "语音附件下载地址不安全")
    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        return
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise VoiceAudioDownloadError("unsafe_url", "语音附件下载地址不安全")


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
