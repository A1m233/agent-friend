"""Validation, serialization, and next-fire calculation for shared schedules."""

from __future__ import annotations

import calendar
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta

from .models import (
    DueWindow,
    FixedIntervalSchedule,
    OnceSchedule,
    RecurringSchedule,
    Schedule,
    ScheduleError,
)

_FREQUENCIES = frozenset({"daily", "weekly", "monthly"})
MIN_FIXED_INTERVAL_SECONDS = 60 * 60


def validate_schedule(schedule: Schedule) -> None:
    if isinstance(schedule, FixedIntervalSchedule):
        anchor = _require_aware(schedule.anchor_at_utc, "anchor_at_utc")
        if schedule.interval_seconds < MIN_FIXED_INTERVAL_SECONDS:
            raise ScheduleError("invalid_schedule", "固定周期最小为 1 小时")
        if schedule.until_at_utc is not None:
            until = _require_aware(schedule.until_at_utc, "until_at_utc")
            if until < anchor:
                raise ScheduleError("invalid_schedule", "until_at_utc 不能早于 anchor_at_utc")
        if schedule.max_occurrences is not None and schedule.max_occurrences < 1:
            raise ScheduleError("invalid_schedule", "max_occurrences 必须大于或等于 1")
        return

    zone = _zone(schedule.timezone)
    if schedule.fold not in {0, 1}:
        raise ScheduleError("invalid_schedule", "fold 只接受 0 或 1")
    if isinstance(schedule, OnceSchedule):
        if schedule.local_datetime.tzinfo is not None:
            raise ScheduleError(
                "invalid_datetime", "一次性 schedule 的 local_datetime 不能自带时区"
            )
        _localize(schedule.local_datetime, zone, schedule.fold, reject_gap=True)
        return

    if schedule.frequency not in _FREQUENCIES:
        raise ScheduleError("invalid_schedule", "frequency 只支持 daily、weekly 或 monthly")
    if schedule.interval < 1:
        raise ScheduleError("invalid_schedule", "interval 必须大于或等于 1")
    if schedule.frequency == "weekly":
        if not schedule.weekdays or any(day < 1 or day > 7 for day in schedule.weekdays):
            raise ScheduleError("invalid_schedule", "weekly 必须提供 ISO weekday 1-7")
    elif schedule.weekdays:
        raise ScheduleError("invalid_schedule", "只有 weekly 可以提供 weekdays")
    if schedule.frequency == "monthly":
        if not schedule.month_days or any(day < 1 or day > 31 for day in schedule.month_days):
            raise ScheduleError("invalid_schedule", "monthly 必须提供 month_days 1-31")
    elif schedule.month_days:
        raise ScheduleError("invalid_schedule", "只有 monthly 可以提供 month_days")
    if schedule.max_occurrences is not None and schedule.max_occurrences < 1:
        raise ScheduleError("invalid_schedule", "max_occurrences 必须大于或等于 1")
    if schedule.until_local is not None:
        if schedule.until_local.tzinfo is not None:
            raise ScheduleError("invalid_datetime", "until_local 不能自带时区")
        start = datetime.combine(schedule.starts_on, schedule.local_time)
        if schedule.until_local < start:
            raise ScheduleError("invalid_schedule", "until_local 不能早于 starts_on")


def first_fire_at(schedule: Schedule) -> datetime | None:
    validate_schedule(schedule)
    if isinstance(schedule, FixedIntervalSchedule):
        anchor = schedule.anchor_at_utc.astimezone(UTC)
        if schedule.until_at_utc is not None and anchor > schedule.until_at_utc.astimezone(UTC):
            return None
        return anchor
    if isinstance(schedule, OnceSchedule):
        localized = _localize(
            schedule.local_datetime,
            _zone(schedule.timezone),
            schedule.fold,
            reject_gap=True,
        )
        if localized is None:  # pragma: no cover - reject_gap raises first
            raise ScheduleError("nonexistent_local_time", "该本地时间不存在")
        return localized.astimezone(UTC)
    return _next_recurring_after(schedule, after_utc=None)


def next_fire_after(
    schedule: Schedule,
    after_utc: datetime,
    *,
    generated_count: int,
) -> datetime | None:
    validate_schedule(schedule)
    after = _require_aware(after_utc, "after_utc").astimezone(UTC)
    if isinstance(schedule, OnceSchedule):
        return None
    if schedule.max_occurrences is not None and generated_count >= schedule.max_occurrences:
        return None
    if isinstance(schedule, FixedIntervalSchedule):
        anchor = schedule.anchor_at_utc.astimezone(UTC)
        elapsed = max(0.0, (after - anchor).total_seconds())
        steps = math.floor(elapsed / schedule.interval_seconds) + 1
        candidate = anchor + timedelta(seconds=steps * schedule.interval_seconds)
        if schedule.until_at_utc is not None and candidate > schedule.until_at_utc.astimezone(UTC):
            return None
        return candidate
    return _next_recurring_after(schedule, after_utc=after)


def advance_due_window(
    schedule: Schedule,
    first_due_at: datetime,
    now: datetime,
    *,
    generated_count: int,
) -> DueWindow:
    validate_schedule(schedule)
    first_due = _require_aware(first_due_at, "first_due_at").astimezone(UTC)
    current = _require_aware(now, "now").astimezone(UTC)
    if first_due > current:
        raise ScheduleError("not_due", "first_due_at 尚未到期")
    if isinstance(schedule, OnceSchedule):
        return DueWindow(first_due, first_due, None, 1)
    if isinstance(schedule, FixedIntervalSchedule):
        available = (
            math.floor((current - first_due).total_seconds() / schedule.interval_seconds) + 1
        )
        if schedule.max_occurrences is not None:
            available = min(available, schedule.max_occurrences - generated_count)
        if available <= 0:
            raise ScheduleError("schedule_exhausted", "schedule 已无可生成 occurrence")
        latest = first_due + timedelta(seconds=(available - 1) * schedule.interval_seconds)
        next_fire = next_fire_after(
            schedule,
            latest,
            generated_count=generated_count + available,
        )
        return DueWindow(first_due, latest, next_fire, available)

    latest = first_due
    due_count = 1
    while True:
        candidate = next_fire_after(
            schedule,
            latest,
            generated_count=generated_count + due_count,
        )
        if candidate is None or candidate > current:
            return DueWindow(first_due, latest, candidate, due_count)
        latest = candidate
        due_count += 1


def schedule_to_dict(schedule: Schedule) -> dict[str, Any]:
    validate_schedule(schedule)
    if isinstance(schedule, OnceSchedule):
        return {
            "kind": "once",
            "local_datetime": schedule.local_datetime.isoformat(),
            "timezone": schedule.timezone,
            "fold": schedule.fold,
        }
    if isinstance(schedule, FixedIntervalSchedule):
        return {
            "kind": "fixed_interval",
            "anchor_at_utc": _dump_utc(schedule.anchor_at_utc),
            "interval_seconds": schedule.interval_seconds,
            "until_at_utc": _dump_utc(schedule.until_at_utc) if schedule.until_at_utc else None,
            "max_occurrences": schedule.max_occurrences,
        }
    return {
        "kind": "recurring",
        "frequency": schedule.frequency,
        "interval": schedule.interval,
        "local_time": schedule.local_time.isoformat(),
        "timezone": schedule.timezone,
        "starts_on": schedule.starts_on.isoformat(),
        "weekdays": list(schedule.weekdays),
        "month_days": list(schedule.month_days),
        "until_local": schedule.until_local.isoformat() if schedule.until_local else None,
        "max_occurrences": schedule.max_occurrences,
        "fold": schedule.fold,
    }


def schedule_from_dict(value: dict[str, Any]) -> Schedule:
    try:
        kind = value["kind"]
        if kind == "once":
            schedule: Schedule = OnceSchedule(
                local_datetime=datetime.fromisoformat(str(value["local_datetime"])),
                timezone=str(value["timezone"]),
                fold=int(value.get("fold", 0)),
            )
        elif kind in {"recurring", "calendar"}:
            until = value.get("until_local")
            schedule = RecurringSchedule(
                frequency=str(value["frequency"]),
                interval=int(value.get("interval", 1)),
                local_time=datetime.strptime(str(value["local_time"]), "%H:%M:%S").time(),
                timezone=str(value["timezone"]),
                starts_on=date.fromisoformat(str(value["starts_on"])),
                weekdays=tuple(sorted({int(item) for item in value.get("weekdays", [])})),
                month_days=tuple(sorted({int(item) for item in value.get("month_days", [])})),
                until_local=datetime.fromisoformat(str(until)) if until else None,
                max_occurrences=(
                    int(value["max_occurrences"])
                    if value.get("max_occurrences") is not None
                    else None
                ),
                fold=int(value.get("fold", 0)),
            )
        elif kind == "fixed_interval":
            until_utc = value.get("until_at_utc")
            schedule = FixedIntervalSchedule(
                anchor_at_utc=_parse_utc(value["anchor_at_utc"]),
                interval_seconds=int(value["interval_seconds"]),
                until_at_utc=_parse_utc(until_utc) if until_utc else None,
                max_occurrences=(
                    int(value["max_occurrences"])
                    if value.get("max_occurrences") is not None
                    else None
                ),
            )
        else:
            raise KeyError("kind")
    except (KeyError, TypeError, ValueError) as exc:
        raise ScheduleError("invalid_schedule", "schedule 结构或字段无效") from exc
    validate_schedule(schedule)
    return schedule


def schedule_summary(schedule: Schedule) -> str:
    if isinstance(schedule, OnceSchedule):
        return "once"
    if isinstance(schedule, FixedIntervalSchedule):
        hours = schedule.interval_seconds / 3600
        return f"every {hours:g} hour(s)"
    if schedule.frequency == "daily":
        return f"every {schedule.interval} day(s)"
    if schedule.frequency == "weekly":
        days = ",".join(str(item) for item in schedule.weekdays)
        return f"every {schedule.interval} week(s) on {days}"
    days = ",".join(str(item) for item in schedule.month_days)
    return f"every {schedule.interval} month(s) on day {days}"


def to_local(instant: datetime, timezone: str) -> datetime:
    return _require_aware(instant, "instant").astimezone(_zone(timezone))


def _next_recurring_after(
    schedule: RecurringSchedule,
    *,
    after_utc: datetime | None,
) -> datetime | None:
    zone = _zone(schedule.timezone)
    start_local = datetime.combine(schedule.starts_on, schedule.local_time)
    if after_utc is None:
        cursor_date = schedule.starts_on
    else:
        cursor_date = max(schedule.starts_on, after_utc.astimezone(zone).date())
    horizon = cursor_date + relativedelta(years=20)
    current = cursor_date
    while current <= horizon:
        if _matches(schedule, current):
            naive = datetime.combine(current, schedule.local_time)
            if schedule.until_local is not None and naive > schedule.until_local:
                return None
            localized = _localize(naive, zone, schedule.fold, reject_gap=False)
            if localized is not None:
                instant = localized.astimezone(UTC)
                if naive >= start_local and (after_utc is None or instant > after_utc):
                    return instant
        current += timedelta(days=1)
    return None


def _matches(schedule: RecurringSchedule, candidate: date) -> bool:
    if candidate < schedule.starts_on:
        return False
    if schedule.frequency == "daily":
        return (candidate - schedule.starts_on).days % schedule.interval == 0
    if schedule.frequency == "weekly":
        weeks = (candidate - schedule.starts_on).days // 7
        return weeks % schedule.interval == 0 and candidate.isoweekday() in schedule.weekdays
    month_delta = (candidate.year - schedule.starts_on.year) * 12
    month_delta += candidate.month - schedule.starts_on.month
    if month_delta < 0 or month_delta % schedule.interval != 0:
        return False
    if candidate.day > calendar.monthrange(candidate.year, candidate.month)[1]:
        return False
    return candidate.day in schedule.month_days


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError("invalid_datetime", f"未知 IANA 时区: {name}") from exc


def _localize(
    naive: datetime,
    zone: ZoneInfo,
    fold: int,
    *,
    reject_gap: bool,
) -> datetime | None:
    candidate = naive.replace(tzinfo=zone, fold=fold)
    round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        if reject_gap:
            raise ScheduleError("nonexistent_local_time", "该本地时间位于夏令时跳跃的不存在区间")
        return None
    return candidate


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ScheduleError("invalid_datetime", f"{field} 必须包含时区")
    return value


def _dump_utc(value: datetime) -> str:
    return _require_aware(value, "datetime").astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _require_aware(parsed, "datetime").astimezone(UTC)
