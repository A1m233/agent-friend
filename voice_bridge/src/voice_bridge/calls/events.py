"""In-memory voice call event fan-out for UI status updates."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VoiceCallEvent:
    """A best-effort voice call event pushed to frontend call windows."""

    type: str
    call_id: str
    stage: str
    speaker: str = ""
    round_id: int | None = None
    definite: bool | None = None
    text_len: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "callId": self.call_id,
            "stage": self.stage,
            "speaker": self.speaker,
            "roundId": self.round_id,
            "definite": self.definite,
            "textLen": self.text_len,
        }


class VoiceCallEventHub:
    """Per-call event queues used by the voice-call UI WebSocket."""

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[VoiceCallEvent]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, call_id: str) -> asyncio.Queue[VoiceCallEvent]:
        queue: asyncio.Queue[VoiceCallEvent] = asyncio.Queue(maxsize=32)
        with self._lock:
            self._queues.setdefault(call_id, set()).add(queue)
        return queue

    def unsubscribe(self, call_id: str, queue: asyncio.Queue[VoiceCallEvent]) -> None:
        with self._lock:
            queues = self._queues.get(call_id)
            if queues is None:
                return
            queues.discard(queue)
            if not queues:
                self._queues.pop(call_id, None)

    def publish(self, event: VoiceCallEvent) -> None:
        with self._lock:
            queues = list(self._queues.get(event.call_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # UI status is transient. Drop stale events rather than blocking callbacks.
                continue
