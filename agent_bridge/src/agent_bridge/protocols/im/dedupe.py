"""Short-lived IM inbound message deduplication."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .event import InboundEvent

__all__ = ["MessageDedupeKey", "RecentMessageDeduper"]


@dataclass(frozen=True)
class MessageDedupeKey:
    im_type: str
    chat_scope: str
    chat_id: str
    message_id: str


class RecentMessageDeduper:
    """In-process TTL/LRU cache for duplicate IM gateway deliveries."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 600.0,
        max_items: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._clock = clock
        self._items: OrderedDict[MessageDedupeKey, float] = OrderedDict()

    def seen_or_mark(self, key: MessageDedupeKey | None) -> bool:
        """Return True when ``key`` is a recent duplicate; otherwise record it."""

        if key is None:
            return False
        now = self._clock()
        self._prune(now)
        if key in self._items:
            self._items.move_to_end(key)
            return True
        self._items[key] = now
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)
        return False

    @staticmethod
    def key_for(im_type: str, event: InboundEvent) -> MessageDedupeKey | None:
        message_id = (event.message_id or "").strip()
        if not message_id:
            return None
        return MessageDedupeKey(
            im_type=im_type,
            chat_scope=event.chat_scope,
            chat_id=event.chat_id,
            message_id=message_id,
        )

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        while self._items:
            _, seen_at = next(iter(self._items.items()))
            if seen_at >= cutoff:
                break
            self._items.popitem(last=False)
