from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from agent.scheduling import (
    FixedIntervalSchedule,
    ScheduleCoordinator,
    ScheduleError,
    advance_due_window,
    first_fire_at,
    next_fire_after,
    schedule_from_dict,
    schedule_to_dict,
)


def test_fixed_interval_round_trip_and_o1_due_window() -> None:
    anchor = datetime(2026, 8, 1, tzinfo=UTC)
    schedule = FixedIntervalSchedule(anchor_at_utc=anchor, interval_seconds=3600)

    assert first_fire_at(schedule) == anchor
    assert next_fire_after(schedule, anchor, generated_count=1) == anchor + timedelta(hours=1)
    window = advance_due_window(
        schedule,
        anchor,
        anchor + timedelta(days=10, minutes=30),
        generated_count=0,
    )
    assert window.due_count == 241
    assert window.latest_due_at == anchor + timedelta(days=10)
    assert window.next_fire_at == anchor + timedelta(days=10, hours=1)
    assert schedule_from_dict(schedule_to_dict(schedule)) == schedule


def test_fixed_interval_rejects_less_than_one_hour() -> None:
    schedule = FixedIntervalSchedule(
        anchor_at_utc=datetime(2026, 8, 1, tzinfo=UTC),
        interval_seconds=3599,
    )
    with pytest.raises(ScheduleError, match="最小为 1 小时"):
        first_fire_at(schedule)


@dataclass
class _Driver:
    name: ClassVar[str] = "test"
    wake_at: datetime | None
    calls: list[datetime]

    def next_wakeup_at(self) -> datetime | None:
        return self.wake_at

    def run_due(self, now: datetime) -> int:
        self.calls.append(now)
        self.wake_at = None
        return 1


@dataclass
class _FutureDriver(_Driver):
    name: ClassVar[str] = "future"


@dataclass
class _BlockingDriver:
    name: ClassVar[str] = "blocking"
    wake_at: datetime
    started: threading.Event
    release: threading.Event
    calls: int = 0

    def next_wakeup_at(self) -> datetime | None:
        return self.wake_at

    def run_due(self, now: datetime) -> int:
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=2)
        self.wake_at = now + timedelta(hours=1)
        return 1


def test_coordinator_run_once_only_calls_due_drivers() -> None:
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    due = _Driver(now, [])
    future = _FutureDriver(now + timedelta(hours=1), [])
    coordinator = ScheduleCoordinator((due, future), clock=lambda: now)

    assert coordinator.run_once() == 1
    assert due.calls == [now]
    assert future.calls == []


def test_coordinator_prevents_driver_reentry_and_bounds_stop_wait() -> None:
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    driver = _BlockingDriver(now, threading.Event(), threading.Event())
    coordinator = ScheduleCoordinator((driver,), clock=lambda: now, idle_poll_seconds=0.01)
    coordinator.start()
    assert driver.started.wait(timeout=1)

    coordinator.wake()
    coordinator.wake()
    assert coordinator.stop(timeout=0.01) is False
    assert driver.calls == 1

    driver.release.set()
    assert coordinator.stop(timeout=1) is True
