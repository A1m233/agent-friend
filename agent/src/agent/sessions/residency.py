"""Idle-only retention policy, independent of desktop transport and run scheduling."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from threading import RLock
from time import monotonic


def retained_bytes(value: object) -> int:
    """Approximate owned Python data; functions, stores and locks are opaque."""
    seen: set[int] = set()
    pending = [value]
    total = 0
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (tuple, list, set, frozenset)):
            pending.extend(item)
        elif is_dataclass(item) and not isinstance(item, type):
            attributes = getattr(item, "__dict__", None)
            if isinstance(attributes, dict):
                pending.append(attributes)
            else:
                pending.extend(getattr(item, field.name) for field in fields(item))
    return total


class SessionResidency:
    def __init__(
        self,
        *,
        max_bytes: int = 256 * 1024**2,
        max_entries: int = 8,
        idle_seconds: float = 600,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._lock = RLock()
        self._users: dict[str, set[object]] = {}
        self._idle: dict[str, tuple[object, float, int, object]] = {}
        self._max_bytes, self._max_entries = max_bytes, max_entries
        self._idle_seconds, self._clock = idle_seconds, clock

    @contextmanager
    def using(self, session_id: str) -> Iterator[None]:
        token = object()
        with self._lock:
            self._users.setdefault(session_id, set()).add(token)
            self._idle.pop(session_id, None)
        try:
            yield
        finally:
            with self._lock:
                users = self._users.get(session_id)
                if users is not None:
                    users.discard(token)
                    if not users:
                        self._users.pop(session_id, None)

    def sweep(
        self,
        residents: Mapping[str, object],
        *,
        protected: Callable[[str], bool],
        size: Callable[[object], int],
        release: Callable[[str, object], bool],
        revision: Callable[[object], object] = lambda state: None,
    ) -> tuple[str, ...]:
        """release must recheck backend activity under its mutation reservation."""
        released: list[str] = []
        with self._lock:
            now = self._clock()
            for sid in tuple(self._idle):
                if sid not in residents:
                    self._idle.pop(sid)
            for sid, state in residents.items():
                if self._users.get(sid) or protected(sid):
                    self._idle.pop(sid, None)
                    continue
                previous = self._idle.get(sid)
                current_revision = revision(state)
                if previous is None or previous[0] is not state or previous[3] != current_revision:
                    self._idle[sid] = (state, now, size(state), current_revision)
            total = sum(entry[2] for entry in self._idle.values())
            count = len(self._idle)
            for sid, (state, since, estimated, _revision) in sorted(
                self._idle.items(), key=lambda item: item[1][1]
            ):
                if (
                    total <= self._max_bytes
                    and count <= self._max_entries
                    and now - since < self._idle_seconds
                ):
                    continue
                if release(sid, state):
                    released.append(sid)
                    self._idle.pop(sid)
                    total -= estimated
                    count -= 1
                else:
                    # A racing activity wins. Its eventual completion starts a new
                    # idle interval, rather than inheriting an expired deadline.
                    self._idle.pop(sid)
                    total -= estimated
                    count -= 1
        return tuple(released)
