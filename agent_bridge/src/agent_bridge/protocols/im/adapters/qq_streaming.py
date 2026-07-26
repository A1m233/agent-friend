"""QQ C2C native streaming message lifecycle."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Literal
from urllib.parse import quote

from qqbot_agent_sdk.api_client import QQApiClient

from ..streaming import OutboundStreamContext

__all__ = ["QQC2CStreamSession"]

logger = logging.getLogger(__name__)

_GENERATING = 1
_DONE = 10


class QQC2CStreamSession:
    """Stateful wrapper around ``POST /v2/users/{openid}/stream_messages``."""

    def __init__(
        self,
        *,
        api: QQApiClient,
        bind_id: str,
        context: OutboundStreamContext,
        msg_seq: int,
    ) -> None:
        self._api = api
        self._context = context
        self._msg_seq = msg_seq
        self._bind_ref = _safe_ref(bind_id)
        self._chat_ref = _safe_ref(context.chat_id)
        self._stream_msg_id: str | None = None
        self._next_index = 0
        self._last_accepted_text = ""
        self._successful_updates = 0
        self._phase: Literal["idle", "streaming", "completed", "failed"] = "idle"
        self._had_failure = False
        self._started_at = time.monotonic()

    @property
    def has_started(self) -> bool:
        return self._stream_msg_id is not None

    @property
    def is_completed(self) -> bool:
        return self._phase == "completed"

    @property
    def is_failed(self) -> bool:
        return self._had_failure

    async def update(self, full_text: str) -> None:
        if not full_text or self._phase in {"completed", "failed"}:
            return
        await self._send(full_text, input_state=_GENERATING, terminal=False)

    async def finish(self, full_text: str) -> None:
        if not self.has_started or self._phase == "completed":
            return
        await self._send(full_text or self._last_accepted_text, input_state=_DONE, terminal=True)

    async def abort(self, full_text: str, reason: str) -> None:
        logger.warning(
            "QQ stream abort stream_trace=%s bind_ref=%s chat_ref=%s reason=%s "
            "content_len=%d started=%s",
            self._context.stream_trace,
            self._bind_ref,
            self._chat_ref,
            _safe_reason(reason),
            len(full_text),
            self.has_started,
        )
        if self.has_started:
            await self._send(
                full_text or self._last_accepted_text,
                input_state=_DONE,
                terminal=True,
            )

    async def _send(self, full_text: str, *, input_state: int, terminal: bool) -> None:
        index = self._next_index
        payload: dict[str, Any] = {
            "input_mode": "replace",
            "input_state": input_state,
            "content_type": "markdown",
            "content_raw": full_text,
            "event_id": self._context.reply_to_message_id,
            "msg_id": self._context.reply_to_message_id,
            "msg_seq": self._msg_seq,
            "index": index,
        }
        if self._stream_msg_id is not None:
            payload["stream_msg_id"] = self._stream_msg_id

        request_started = time.monotonic()
        phase = "finish" if terminal else "update"
        try:
            response = await self._api.request(
                "POST",
                f"/v2/users/{quote(self._context.chat_id, safe='')}/stream_messages",
                payload,
            )
        except Exception as exc:
            self._record_failure()
            logger.warning(
                "QQ stream request failed stream_trace=%s run_id=%s phase=%s index=%d "
                "content_len=%d elapsed_ms=%d bind_ref=%s chat_ref=%s reason=%s",
                self._context.stream_trace,
                self._context.run_id or "",
                phase,
                index,
                len(full_text),
                _elapsed_ms(request_started),
                self._bind_ref,
                self._chat_ref,
                _safe_reason(exc, redact=(full_text, self._context.chat_id)),
            )
            return

        code = response.get("code", 0) if isinstance(response, dict) else 0
        if code:
            self._record_failure()
            logger.warning(
                "QQ stream platform rejected stream_trace=%s run_id=%s phase=%s index=%d "
                "content_len=%d elapsed_ms=%d code=%s bind_ref=%s chat_ref=%s reason=%s",
                self._context.stream_trace,
                self._context.run_id or "",
                phase,
                index,
                len(full_text),
                _elapsed_ms(request_started),
                code,
                self._bind_ref,
                self._chat_ref,
                _safe_reason(
                    response.get("message", ""),
                    redact=(full_text, self._context.chat_id),
                ),
            )
            return

        if self._stream_msg_id is None:
            response_id = _response_id(response)
            if not response_id:
                self._record_failure()
                logger.warning(
                    "QQ stream response missing id stream_trace=%s run_id=%s phase=%s "
                    "index=%d content_len=%d bind_ref=%s chat_ref=%s",
                    self._context.stream_trace,
                    self._context.run_id or "",
                    phase,
                    index,
                    len(full_text),
                    self._bind_ref,
                    self._chat_ref,
                )
                return
            self._stream_msg_id = response_id

        self._last_accepted_text = full_text
        self._successful_updates += 1
        self._next_index += 1
        self._phase = "completed" if terminal else "streaming"
        logger.info(
            "QQ stream accepted stream_trace=%s run_id=%s phase=%s index=%d "
            "content_len=%d elapsed_ms=%d update_count=%d bind_ref=%s chat_ref=%s "
            "stream_ref=%s",
            self._context.stream_trace,
            self._context.run_id or "",
            phase,
            index,
            len(full_text),
            _elapsed_ms(request_started),
            self._successful_updates,
            self._bind_ref,
            self._chat_ref,
            _safe_ref(self._stream_msg_id),
        )
        if terminal:
            logger.info(
                "QQ stream completed stream_trace=%s run_id=%s update_count=%d "
                "content_len=%d total_elapsed_ms=%d had_failure=%s",
                self._context.stream_trace,
                self._context.run_id or "",
                self._successful_updates,
                len(full_text),
                _elapsed_ms(self._started_at),
                self._had_failure,
            )

    def _record_failure(self) -> None:
        self._had_failure = True
        if not self.has_started:
            self._phase = "failed"


def _response_id(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    value = response.get("id")
    return str(value) if isinstance(value, (str, int)) and str(value) else ""


def _safe_ref(value: object) -> str:
    if value in (None, ""):
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _safe_reason(value: object, *, redact: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in redact:
        if secret:
            text = text.replace(secret, "<redacted>")
    return " ".join(text.split())[:240]


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))
