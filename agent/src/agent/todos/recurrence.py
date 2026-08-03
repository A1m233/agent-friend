"""受控 Reminder 周期规则的校验、序列化与下一次时间计算。"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta

from .models import OnceSchedule, RecurringSchedule, ReminderSchedule, TodoError

_FREQUENCIES = frozenset({"daily", "weekly", "monthly"})


def validate_schedule(schedule: ReminderSchedule) -> None:
    zone = _zone(schedule.timezone)
    if schedule.fold not in {0, 1}:
        raise TodoError("invalid_recurrence", "fold 只接受 0 或 1")
    if isinstance(schedule, OnceSchedule):
        if schedule.local_datetime.tzinfo is not None:
            raise TodoError("invalid_datetime", "一次性提醒的 local_datetime 不能自带时区")
        _localize(schedule.local_datetime, zone, schedule.fold, reject_gap=True)
        return

    if schedule.frequency not in _FREQUENCIES:
        raise TodoError("invalid_recurrence", "frequency 只支持 daily、weekly 或 monthly")
    if schedule.interval < 1:
        raise TodoError("invalid_recurrence", "interval 必须大于或等于 1")
    if schedule.frequency == "weekly":
        if not schedule.weekdays or any(day < 1 or day > 7 for day in schedule.weekdays):
            raise TodoError("invalid_recurrence", "weekly 必须提供 ISO weekday 1-7")
    elif schedule.weekdays:
        raise TodoError("invalid_recurrence", "只有 weekly 可以提供 weekdays")
    if schedule.frequency == "monthly":
        if not schedule.month_days or any(day < 1 or day > 31 for day in schedule.month_days):
            raise TodoError("invalid_recurrence", "monthly 必须提供 month_days 1-31")
    elif schedule.month_days:
        raise TodoError("invalid_recurrence", "只有 monthly 可以提供 month_days")
    if schedule.max_occurrences is not None and schedule.max_occurrences < 1:
        raise TodoError("invalid_recurrence", "max_occurrences 必须大于或等于 1")
    if schedule.until_local is not None:
        if schedule.until_local.tzinfo is not None:
            raise TodoError("invalid_datetime", "until_local 不能自带时区")
        start = datetime.combine(schedule.starts_on, schedule.local_time)
        if schedule.until_local < start:
            raise TodoError("invalid_recurrence", "until_local 不能早于 starts_on")


def first_fire_at(schedule: ReminderSchedule) -> datetime | None:
    validate_schedule(schedule)
    if isinstance(schedule, OnceSchedule):
        localized = _localize(
            schedule.local_datetime, _zone(schedule.timezone), schedule.fold, reject_gap=True
        )
        if localized is None:  # reject_gap=True raises first; keep the type boundary explicit.
            raise TodoError("nonexistent_local_time", "该本地时间不存在")
        return localized.astimezone(UTC)
    return _next_recurring_after(schedule, after_utc=None)


def next_fire_after(
    schedule: ReminderSchedule,
    after_utc: datetime,
    *,
    generated_count: int,
) -> datetime | None:
    validate_schedule(schedule)
    if isinstance(schedule, OnceSchedule):
        return None
    if schedule.max_occurrences is not None and generated_count >= schedule.max_occurrences:
        return None
    if after_utc.tzinfo is None:
        raise TodoError("invalid_datetime", "after_utc 必须包含时区")
    return _next_recurring_after(schedule, after_utc=after_utc.astimezone(UTC))


def schedule_to_dict(schedule: ReminderSchedule) -> dict[str, Any]:
    validate_schedule(schedule)
    if isinstance(schedule, OnceSchedule):
        return {
            "kind": "once",
            "local_datetime": schedule.local_datetime.isoformat(),
            "timezone": schedule.timezone,
            "fold": schedule.fold,
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


def schedule_from_dict(value: dict[str, Any]) -> ReminderSchedule:
    try:
        kind = value["kind"]
        if kind == "once":
            schedule: ReminderSchedule = OnceSchedule(
                local_datetime=datetime.fromisoformat(str(value["local_datetime"])),
                timezone=str(value["timezone"]),
                fold=int(value.get("fold", 0)),
            )
        elif kind == "recurring":
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
        else:
            raise KeyError("kind")
    except (KeyError, TypeError, ValueError) as exc:
        raise TodoError(
            "invalid_recurrence",
            (
                "Reminder schedule 格式无效：一次性提醒需要 kind=once、local_datetime "
                "和 timezone；周期提醒需要 kind=recurring、frequency、local_time、"
                "timezone 和 starts_on"
            ),
        ) from exc
    validate_schedule(schedule)
    return schedule


def schedule_summary(schedule: ReminderSchedule) -> str:
    if isinstance(schedule, OnceSchedule):
        return "once"
    if schedule.frequency == "daily":
        return f"every {schedule.interval} day(s)"
    if schedule.frequency == "weekly":
        days = ",".join(str(item) for item in schedule.weekdays)
        return f"every {schedule.interval} week(s) on {days}"
    days = ",".join(str(item) for item in schedule.month_days)
    return f"every {schedule.interval} month(s) on day {days}"


def to_local(instant: datetime, timezone: str) -> datetime:
    if instant.tzinfo is None:
        raise TodoError("invalid_datetime", "UTC instant 必须包含时区")
    return instant.astimezone(_zone(timezone))


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

    # The bounded horizon prevents a malformed or exhausted rule from looping forever.
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
        raise TodoError("invalid_datetime", f"未知 IANA 时区: {name}") from exc


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
            raise TodoError("nonexistent_local_time", "该本地时间位于夏令时跳跃的不存在区间")
        return None
    return candidate
