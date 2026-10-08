"""Bounded in-flight append capture for an initial business projection build."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock

from ..events import ROLLOUT_EVENT_TYPES, Event
from ..query import SessionQuerySnapshot
from ..store_types import SourceVersion


@dataclass
class BuildTail:
    batches: list[tuple[SourceVersion, SourceVersion, list[Event | bytes]]] = field(
        default_factory=list
    )
    size: int = 0
    valid: bool = True


class BusinessBuilds:
    def __init__(self) -> None:
        self.lock = RLock()
        self._active: dict[str, BuildTail] = {}

    def catch_up(self, tail: BuildTail, snapshot: SessionQuerySnapshot) -> SessionQuerySnapshot:
        with self.lock:
            batches = tuple(tail.batches) if tail.valid else ()
        # Decode retained business bytes outside the writer/capture locks.
        for before, after, records in batches:
            if snapshot.source_version == before:
                events = [
                    Event.from_jsonl(record.decode("utf-8"))
                    if isinstance(record, bytes)
                    else record
                    for record in records
                ]
                snapshot = snapshot.appended(events, after)
        return snapshot

    @contextmanager
    def capture(self, session_id: str) -> Iterator[BuildTail]:
        tail = BuildTail()
        with self.lock:
            self._active[session_id] = tail
        try:
            yield tail
        finally:
            with self.lock:
                self._active.pop(session_id, None)

    def appended(
        self,
        session_id: str,
        before: SourceVersion,
        after: SourceVersion,
        events: list[Event],
        encoded: list[bytes],
        clean: bool,
    ) -> None:
        with self.lock:
            tail = self._active.get(session_id)
            if tail is None or not tail.valid:
                return
            if not clean:
                tail.valid = False
                tail.batches.clear()
                return
            # Internal payloads are not needed by appended(), but their raw positions are.
            retained: list[Event | bytes] = [
                Event(type=event.type, uuid=event.uuid, ts=event.ts, payload={})
                if event.type in ROLLOUT_EVENT_TYPES
                else data
                for event, data in zip(events, encoded, strict=True)
            ]
            tail.size += sum(
                len(record) if isinstance(record, bytes) else 128 for record in retained
            )
            if tail.size > 8 * 1024 * 1024 or len(tail.batches) >= 256:
                tail.valid = False
                tail.batches.clear()
                return
            tail.batches.append((before, after, retained))
