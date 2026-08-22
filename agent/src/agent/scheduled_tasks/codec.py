"""Trusted decoding and JSON projection for Scheduled Task schedules and records."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from ..scheduling import (
    FixedIntervalSchedule,
    OnceSchedule,
    RecurringSchedule,
    Schedule,
    ScheduleError,
    schedule_to_dict,
    validate_schedule,
)
from .models import ScheduledOccurrence, ScheduledTask, ScheduledTaskError


def schedule_from_input(value: dict[str, Any], *, now: datetime) -> Schedule:
    try:
        if now.tzinfo is None:
            raise ValueError("now 必须包含时区")
        current = now.astimezone(UTC)
        kind = str(value["kind"])
        if kind == "once":
            timezone = _required_string(value, "timezone")
            local = value.get("local_datetime")
            delay = value.get("relative_delay")
            if (local is None) == (delay is None):
                raise ValueError("once 必须且只能提供 local_datetime 或 relative_delay")
            if local is not None:
                local_datetime = datetime.fromisoformat(_required_string(value, "local_datetime"))
                if local_datetime.tzinfo is not None:
                    raise ValueError("local_datetime 不能包含 UTC offset")
            else:
                delay_value = _mapping(delay, "relative_delay")
                amount = _required_int(delay_value, "amount")
                if amount < 1:
                    raise ValueError("relative_delay.amount 必须大于 0")
                unit = str(delay_value["unit"])
                factors = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}
                if unit not in factors:
                    raise ValueError("relative_delay.unit 只支持 minute/hour/day/week")
                from zoneinfo import ZoneInfo

                local_datetime = (
                    current.astimezone(ZoneInfo(timezone))
                    + timedelta(seconds=amount * factors[unit])
                ).replace(tzinfo=None)
            schedule: Schedule = OnceSchedule(
                local_datetime=local_datetime,
                timezone=timezone,
                fold=_integer(value, "fold", default=0),
            )
        elif kind == "fixed_interval":
            amount = _required_int(value, "interval")
            if amount < 1:
                raise ValueError("interval 必须大于 0")
            unit = str(value["unit"])
            factors = {"hour": 3600, "day": 86400, "week": 604800}
            if unit not in factors:
                raise ValueError("fixed_interval.unit 只支持 hour/day/week")
            seconds = amount * factors[unit]
            starts_at = value.get("starts_at")
            anchor = (
                _aware_datetime(starts_at, "starts_at")
                if starts_at is not None
                else current + timedelta(seconds=seconds)
            )
            until = value.get("until_at")
            schedule = FixedIntervalSchedule(
                anchor_at_utc=anchor.astimezone(UTC),
                interval_seconds=seconds,
                until_at_utc=(
                    _aware_datetime(until, "until_at").astimezone(UTC)
                    if until is not None
                    else None
                ),
                max_occurrences=_optional_int(value, "max_occurrences"),
            )
        elif kind == "calendar":
            until_local = value.get("until_local")
            parsed_until = (
                datetime.fromisoformat(_required_string(value, "until_local"))
                if until_local is not None
                else None
            )
            if parsed_until is not None and parsed_until.tzinfo is not None:
                raise ValueError("until_local 不能包含 UTC offset")
            schedule = RecurringSchedule(
                frequency=_required_string(value, "frequency"),
                interval=_integer(value, "interval", default=1),
                local_time=time.fromisoformat(_required_string(value, "local_time")),
                timezone=_required_string(value, "timezone"),
                starts_on=date.fromisoformat(_required_string(value, "starts_on")),
                weekdays=tuple(sorted(set(_integer_list(value, "weekdays")))),
                month_days=tuple(sorted(set(_integer_list(value, "month_days")))),
                until_local=parsed_until,
                max_occurrences=_optional_int(value, "max_occurrences"),
                fold=_integer(value, "fold", default=0),
            )
        else:
            raise ValueError("kind 只支持 once/fixed_interval/calendar")
        validate_schedule(schedule)
        return schedule
    except ScheduleError as exc:
        raise ScheduledTaskError(exc.kind, exc.message, exc.details or {}) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ScheduledTaskError("invalid_schedule", str(exc)) from exc


def task_to_dict(task: ScheduledTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "instruction": task.instruction,
        "schedule": schedule_to_dict(task.schedule),
        "next_fire_at_utc": _dt(task.next_fire_at_utc),
        "status": task.status.value,
        "persona_id": task.persona_id,
        "persona_name": task.persona_name,
        "model_id": task.model_id,
        "skill_id": task.skill_id,
        "origin_session_id": task.origin_session_id,
        "fire_count": task.fire_count,
        "revision": task.revision,
        "created_at": _dt(task.created_at),
        "updated_at": _dt(task.updated_at),
        "deleted_at": _dt(task.deleted_at),
    }


def occurrence_to_dict(
    occurrence: ScheduledOccurrence,
    *,
    waiting_for_approval: bool = False,
) -> dict[str, Any]:
    return {
        "id": occurrence.id,
        "task_id": occurrence.task_id,
        "task_revision": occurrence.task_revision,
        "trigger_kind": occurrence.trigger_kind.value,
        "scheduled_at_utc": _dt(occurrence.scheduled_at_utc),
        "state": occurrence.state.value,
        "outcome": occurrence.outcome.value if occurrence.outcome else None,
        "reason_code": occurrence.reason_code,
        "coalesced_count": occurrence.coalesced_count,
        "coalesced_from_utc": _dt(occurrence.coalesced_from_utc),
        "session_id": occurrence.session_id,
        "run_id": occurrence.run_id,
        "waiting_for_approval": waiting_for_approval,
        "skill_source": occurrence.skill_source,
        "skill_version": occurrence.skill_version,
        "created_at": _dt(occurrence.created_at),
        "updated_at": _dt(occurrence.updated_at),
        "started_at": _dt(occurrence.started_at),
        "finished_at": _dt(occurrence.finished_at),
    }


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} 必须是 object")
    return dict(value)


def _required_string(value: dict[str, Any], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return raw.strip()


def _optional_int(value: dict[str, Any], field: str) -> int | None:
    raw = value.get(field)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{field} 必须是整数")
    return raw


def _required_int(value: dict[str, Any], field: str) -> int:
    if field not in value:
        raise KeyError(field)
    return _integer(value, field)


def _integer(value: dict[str, Any], field: str, *, default: int | None = None) -> int:
    raw = value.get(field, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{field} 必须是整数")
    return raw


def _integer_list(value: dict[str, Any], field: str) -> list[int]:
    raw = value.get(field, [])
    if not isinstance(raw, list):
        raise ValueError(f"{field} 必须是 array")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in raw):
        raise ValueError(f"{field} 的每一项都必须是整数")
    return raw


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 ISO 日期时间字符串")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 必须包含 UTC offset")
    return parsed


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
