"""Source-bound last chat timestamp used to order the navigation list."""

import json
import mmap
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from ..errors import SessionCorruptError
from ..events import ALLOWED_EVENT_TYPES, Event
from ..query import CancelCheck
from ..store_types import SourceVersion
from .configuration import write_cache
from .journal import _record_starts, records_reverse

_MESSAGES = {"user_message", "assistant_message"}
_STARTS = _record_starts(ALLOWED_EVENT_TYPES - _MESSAGES)


class ActivityTimes:
    def __init__(self, root: Path) -> None:
        self.root = root / ".activity"

    def path(self, sid: str) -> Path:
        return self.root / (sha256(sid.encode()).hexdigest() + ".json")

    def read(self, sid: str, version: SourceVersion) -> datetime | None:
        try:
            record = json.loads(self.path(sid).read_text(encoding="utf-8"))
            if record["source"] == list(version):
                stamp = datetime.fromisoformat(record["ts"])
                return stamp if stamp.tzinfo is not None else None
        except (OSError, TypeError, ValueError, KeyError):
            pass
        return None

    def publish(self, sid: str, version: SourceVersion, stamp: datetime) -> None:
        write_cache(self.path(sid), {"source": version, "ts": stamp.isoformat()})

    def advance(
        self, sid: str, before: SourceVersion, after: SourceVersion, events: list[Event]
    ) -> None:
        stamp = next((event.ts for event in reversed(events) if event.type in _MESSAGES), None)
        if stamp is None:
            stamp = self.read(sid, before)
        if stamp is not None:
            self.publish(sid, after, stamp)

    def delete(self, sid: str) -> None:
        self.path(sid).unlink(missing_ok=True)


def latest_timestamp(path: Path, fallback: datetime, check: CancelCheck) -> datetime:
    with path.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for start, stop, kind in records_reverse(data, len(data), check, _STARTS):
            if kind is not None and kind not in _MESSAGES:
                continue
            try:
                event = Event.from_jsonl(data[start:stop].decode())
            except (UnicodeDecodeError, SessionCorruptError):
                continue
            if event.type in _MESSAGES:
                return event.ts
    return fallback
