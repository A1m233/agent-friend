"""Shared, persistence-safe schedule domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any


@dataclass(frozen=True)
class OnceSchedule:
    local_datetime: datetime
    timezone: str
    fold: int = 0


@dataclass(frozen=True)
class RecurringSchedule:
    frequency: str
    interval: int
    local_time: time
    timezone: str
    starts_on: date
    weekdays: tuple[int, ...] = ()
    month_days: tuple[int, ...] = ()
    until_local: datetime | None = None
    max_occurrences: int | None = None
    fold: int = 0


CalendarSchedule = RecurringSchedule


@dataclass(frozen=True)
class FixedIntervalSchedule:
    anchor_at_utc: datetime
    interval_seconds: int
    until_at_utc: datetime | None = None
    max_occurrences: int | None = None


Schedule = OnceSchedule | RecurringSchedule | FixedIntervalSchedule


@dataclass(frozen=True)
class DueWindow:
    earliest_due_at: datetime
    latest_due_at: datetime
    next_fire_at: datetime | None
    due_count: int


@dataclass
class ScheduleError(Exception):
    kind: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message
