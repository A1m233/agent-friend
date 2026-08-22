"""Minimal protocol implemented by persistent schedule domains."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Protocol


class ScheduleDriver(Protocol):
    name: ClassVar[str]

    def next_wakeup_at(self) -> datetime | None: ...

    def run_due(self, now: datetime) -> int: ...
