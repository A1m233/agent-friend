"""Platform-neutral IM text streaming contracts and throttled delta relay."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .content import OutboundTextFormat

__all__ = [
    "IMStreamSession",
    "IMTextStreamingProvider",
    "OutboundStreamContext",
    "StreamRelay",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundStreamContext:
    """Stable routing metadata for one platform stream."""

    chat_id: str
    chat_scope: str
    reply_to_message_id: str
    text_format: OutboundTextFormat
    stream_trace: str
    run_id: str | None = None


class IMStreamSession(Protocol):
    """One provider-owned streaming message lifecycle."""

    @property
    def has_started(self) -> bool:
        """Whether the platform accepted at least one streaming update."""

    @property
    def is_completed(self) -> bool:
        """Whether the platform accepted the terminal update."""

    @property
    def is_failed(self) -> bool:
        """Whether any streaming request has failed."""

    async def update(self, full_text: str) -> None:
        """Replace the visible message with the latest cumulative text."""

    async def finish(self, full_text: str) -> None:
        """Send the terminal cumulative text."""

    async def abort(self, full_text: str, reason: str) -> None:
        """Best-effort terminal update for an interrupted Agent turn."""


@runtime_checkable
class IMTextStreamingProvider(Protocol):
    """Optional provider capability for native text streaming."""

    def open_text_stream(self, context: OutboundStreamContext) -> IMStreamSession | None:
        """Create a local stream session, or return ``None`` when ineligible."""


class StreamRelay:
    """Accumulate text deltas and throttle writes to a stream session.

    The relay is consumed by exactly one asyncio task. ``feed`` never creates
    background requests, so provider calls remain strictly ordered. The first
    non-empty delta is sent immediately; later updates are coalesced until the
    configured interval has elapsed. ``finish`` is intentionally owned by the
    router so it can use the authoritative final Agent output.
    """

    def __init__(
        self,
        session: IMStreamSession,
        *,
        stream_trace: str,
        throttle_seconds: float = 0.5,
    ) -> None:
        self._session = session
        self._stream_trace = stream_trace
        self._throttle_seconds = max(0.0, throttle_seconds)
        self._parts: list[str] = []
        self._last_attempt_at: float | None = None
        self._last_sent_text = ""

    @property
    def full_text(self) -> str:
        return "".join(self._parts)

    @property
    def session(self) -> IMStreamSession:
        return self._session

    async def feed(self, delta: str) -> None:
        if not delta:
            return
        self._parts.append(delta)
        if self._session.is_failed:
            return

        now = time.monotonic()
        should_send = not self._session.has_started
        if self._last_attempt_at is not None:
            should_send = should_send or now - self._last_attempt_at >= self._throttle_seconds
        if not should_send:
            logger.debug(
                "IM stream update coalesced stream_trace=%s content_len=%d",
                self._stream_trace,
                len(self.full_text),
            )
            return

        self._last_attempt_at = now
        snapshot = self.full_text
        await self._session.update(snapshot)
        if not self._session.is_failed:
            self._last_sent_text = snapshot

    async def flush(self) -> None:
        """Send a coalesced trailing snapshot before the terminal update."""

        snapshot = self.full_text
        if not snapshot or snapshot == self._last_sent_text or self._session.is_failed:
            return
        self._last_attempt_at = time.monotonic()
        await self._session.update(snapshot)
        if not self._session.is_failed:
            self._last_sent_text = snapshot

    async def feed_from_queue(self, queue: asyncio.Queue[object], end_marker: object) -> None:
        """Consume deltas until ``end_marker``, draining bursts before each feed."""

        while True:
            item = await queue.get()
            if item is end_marker:
                return
            if not isinstance(item, str):
                continue

            parts = [item]
            reached_end = False
            while True:
                try:
                    queued = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is end_marker:
                    reached_end = True
                    break
                if isinstance(queued, str):
                    parts.append(queued)
            await self.feed("".join(parts))
            if reached_end:
                return
