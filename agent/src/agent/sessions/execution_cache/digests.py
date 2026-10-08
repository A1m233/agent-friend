"""In-process hashes of fully read prefixes, advanced only by durable appends."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from threading import RLock
from typing import Protocol

from ..store_types import SourceVersion


class SourceHasher(Protocol):
    def update(self, data: bytes) -> None: ...

    def copy(self) -> SourceHasher: ...

    def hexdigest(self) -> str: ...


class SourceDigestTracker:
    """No hash state is persisted; a new process must read every original byte."""

    def __init__(self, *, max_entries: int = 512) -> None:
        self._entries: OrderedDict[str, tuple[SourceVersion, SourceHasher]] = OrderedDict()
        self._lock = RLock()
        self._max_entries = max_entries

    def accept(self, session_id: str, version: SourceVersion, hasher: SourceHasher) -> None:
        with self._lock:
            self._entries[session_id] = (version, hasher.copy())
            self._entries.move_to_end(session_id)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def advance(
        self,
        session_id: str,
        before: SourceVersion,
        after: SourceVersion,
        chunks: Iterable[bytes],
        *,
        clean_boundary: bool,
    ) -> None:
        with self._lock:
            previous = self._entries.pop(session_id, None)
            if previous is None or previous[0] != before or not clean_boundary:
                return
            if before[:2] != after[:2] or before[2] > after[2]:
                return
            hasher = previous[1].copy()
            appended_bytes = 0
            for chunk in chunks:
                hasher.update(chunk)
                appended_bytes += len(chunk)
            if before[2] + appended_bytes != after[2]:
                return
            self._entries[session_id] = (after, hasher)

    def digest(self, session_id: str, version: SourceVersion) -> str | None:
        with self._lock:
            entry = self._entries.get(session_id)
            return entry[1].hexdigest() if entry is not None and entry[0] == version else None

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(session_id, None)
