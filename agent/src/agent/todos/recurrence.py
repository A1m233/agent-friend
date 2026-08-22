"""Todo-compatible wrapper around shared schedule recurrence primitives."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..scheduling import OnceSchedule, RecurringSchedule, ScheduleError
from ..scheduling import first_fire_at as _first_fire_at
from ..scheduling import next_fire_after as _next_fire_after
from ..scheduling import schedule_from_dict as _schedule_from_dict
from ..scheduling import schedule_summary as _schedule_summary
from ..scheduling import schedule_to_dict as _schedule_to_dict
from ..scheduling import to_local as _to_local
from ..scheduling import validate_schedule as _validate_schedule
from .models import ReminderSchedule, TodoError


def _todo_errors[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except ScheduleError as exc:
        kind = "invalid_recurrence" if exc.kind == "invalid_schedule" else exc.kind
        raise TodoError(kind, exc.message, exc.details or {}) from exc


def validate_schedule(schedule: ReminderSchedule) -> None:
    _todo_errors(lambda: _validate_schedule(schedule))


def first_fire_at(schedule: ReminderSchedule) -> datetime | None:
    return _todo_errors(lambda: _first_fire_at(schedule))


def next_fire_after(
    schedule: ReminderSchedule,
    after_utc: datetime,
    *,
    generated_count: int,
) -> datetime | None:
    return _todo_errors(
        lambda: _next_fire_after(schedule, after_utc, generated_count=generated_count)
    )


def schedule_to_dict(schedule: ReminderSchedule) -> dict[str, Any]:
    return _todo_errors(lambda: _schedule_to_dict(schedule))


def schedule_from_dict(value: dict[str, Any]) -> ReminderSchedule:
    try:
        result = _schedule_from_dict(value)
    except ScheduleError as exc:
        if exc.kind == "invalid_schedule" and exc.message == "schedule 结构或字段无效":
            raise TodoError(
                "invalid_recurrence",
                (
                    "Reminder schedule 格式无效：一次性提醒需要 kind=once、local_datetime "
                    "和 timezone；周期提醒需要 kind=recurring、frequency、local_time、"
                    "timezone 和 starts_on"
                ),
            ) from exc
        kind = "invalid_recurrence" if exc.kind == "invalid_schedule" else exc.kind
        raise TodoError(kind, exc.message, exc.details or {}) from exc
    if not isinstance(result, (OnceSchedule, RecurringSchedule)):
        raise TodoError("invalid_recurrence", "Reminder 不支持 fixed_interval schedule")
    return result


def schedule_summary(schedule: ReminderSchedule) -> str:
    return _todo_errors(lambda: _schedule_summary(schedule))


def to_local(instant: datetime, timezone: str) -> datetime:
    return _todo_errors(lambda: _to_local(instant, timezone))
