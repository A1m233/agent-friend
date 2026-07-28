from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from agent.todos import (
    OnceSchedule,
    RecurringSchedule,
    TodoError,
    first_fire_at,
    next_fire_after,
    schedule_from_dict,
    schedule_to_dict,
)


def test_once_round_trip_and_utc_conversion() -> None:
    schedule = OnceSchedule(
        local_datetime=datetime(2026, 7, 29, 9, 0),
        timezone="Asia/Shanghai",
    )
    assert first_fire_at(schedule) == datetime(2026, 7, 29, 1, 0, tzinfo=UTC)
    assert schedule_from_dict(schedule_to_dict(schedule)) == schedule


def test_once_rejects_nonexistent_dst_time() -> None:
    schedule = OnceSchedule(
        local_datetime=datetime(2026, 3, 8, 2, 30),
        timezone="America/New_York",
    )
    with pytest.raises(TodoError, match="不存在区间") as caught:
        first_fire_at(schedule)
    assert caught.value.kind == "nonexistent_local_time"


def test_ambiguous_time_respects_fold() -> None:
    first = first_fire_at(
        OnceSchedule(
            local_datetime=datetime(2026, 11, 1, 1, 30),
            timezone="America/New_York",
            fold=0,
        )
    )
    second = first_fire_at(
        OnceSchedule(
            local_datetime=datetime(2026, 11, 1, 1, 30),
            timezone="America/New_York",
            fold=1,
        )
    )
    assert first is not None and second is not None
    assert (second - first).total_seconds() == 3600


def test_monthly_missing_day_is_skipped_instead_of_clamped() -> None:
    schedule = RecurringSchedule(
        frequency="monthly",
        interval=1,
        local_time=time(9),
        timezone="UTC",
        starts_on=date(2026, 1, 31),
        month_days=(31,),
    )
    first = first_fire_at(schedule)
    assert first == datetime(2026, 1, 31, 9, tzinfo=UTC)
    assert next_fire_after(schedule, first, generated_count=1) == datetime(
        2026, 3, 31, 9, tzinfo=UTC
    )


def test_weekly_interval_and_weekdays() -> None:
    schedule = RecurringSchedule(
        frequency="weekly",
        interval=2,
        local_time=time(9),
        timezone="UTC",
        starts_on=date(2026, 7, 27),
        weekdays=(1, 4),
    )
    first = first_fire_at(schedule)
    assert first == datetime(2026, 7, 27, 9, tzinfo=UTC)
    second = next_fire_after(schedule, first, generated_count=1)
    assert second == datetime(2026, 7, 30, 9, tzinfo=UTC)
    assert next_fire_after(schedule, second, generated_count=2) == datetime(
        2026, 8, 10, 9, tzinfo=UTC
    )


def test_recurring_gap_is_skipped() -> None:
    schedule = RecurringSchedule(
        frequency="daily",
        interval=1,
        local_time=time(2, 30),
        timezone="America/New_York",
        starts_on=date(2026, 3, 8),
    )
    assert first_fire_at(schedule) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_max_occurrences_stops_rule() -> None:
    schedule = RecurringSchedule(
        frequency="daily",
        interval=1,
        local_time=time(9),
        timezone="UTC",
        starts_on=date(2026, 7, 28),
        max_occurrences=1,
    )
    first = first_fire_at(schedule)
    assert first is not None
    assert next_fire_after(schedule, first, generated_count=1) is None
