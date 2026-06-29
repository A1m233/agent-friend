"""HTTP client for voice_bridge one-shot transcription."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

__all__ = [
    "VoiceBridgeTranscription",
    "VoiceBridgeTranscriptionClient",
    "VoiceBridgeTranscriptionError",
]


@dataclass(frozen=True)
class VoiceBridgeTranscription:
    text: str
    trace_id: str
    source: str = "voice_bridge"


class VoiceBridgeTranscriptionError(Exception):
    """Raised when voice_bridge cannot transcribe an audio payload."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(detail or message)
        self.code = code
        self.message = message
        self.detail = detail


class VoiceBridgeTranscriptionClient:
    """Small, stateless client for ``/voice/transcriptions/audio``."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: httpx.Timeout | float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or httpx.Timeout(connect=2.0, read=30.0, write=10.0, pool=2.0)

    async def transcribe_wav(
        self,
        audio: bytes,
        *,
        trace_id: str,
        locale: str | None = None,
    ) -> VoiceBridgeTranscription:
        params: dict[str, Any] = {"trace_id": trace_id, "format": "wav"}
        if locale:
            params["locale"] = locale
        url = f"{self._base_url}/voice/transcriptions/audio"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    params=params,
                    content=audio,
                    headers={"content-type": "audio/wav"},
                )
        except httpx.HTTPError as e:
            raise VoiceBridgeTranscriptionError(
                "voice_bridge_unreachable",
                "语音识别服务暂时不可用",
                detail=str(e),
            ) from e

        payload = _json_payload(response)
        if response.status_code >= 400:
            raise VoiceBridgeTranscriptionError(
                str(payload.get("code") or "voice_bridge_error"),
                str(payload.get("message") or "语音识别暂时失败"),
                detail=f"status={response.status_code}",
            )

        text = str(payload.get("text") or "").strip()
        if not text:
            raise VoiceBridgeTranscriptionError(
                "empty_transcript",
                "语音识别暂时没有得到结果",
                detail=f"trace_id={trace_id}",
            )
        return VoiceBridgeTranscription(
            text=text,
            trace_id=str(payload.get("traceId") or trace_id),
            source=str(payload.get("source") or "voice_bridge"),
        )


def _json_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}
