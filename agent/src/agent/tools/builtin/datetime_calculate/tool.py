"""Structured date arithmetic with explicit timezone semantics."""

from __future__ import annotations

import calendar
import json
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta

from ...protocol import ToolResult

_RUNTIME_NOW = "__agent_friend_runtime_now"
_RUNTIME_TIMEZONE = "__agent_friend_runtime_timezone"


class _DateTimeInputError(ValueError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class DateTimeCalculateTool:
    name: ClassVar[str] = "datetime_calculate"
    result_context_retention: ClassVar[str] = "recoverable"
    execution_kind: ClassVar[str] = "parallel_read"
    timeout_seconds: ClassVar[float] = 5.0
    description: ClassVar[str] = (
        "把绝对日期时间说成今天、明天、今晚、明早或昨天时，若运行时日历锚点不能直接覆盖目标，"
        "必须调用此工具的 calendar_relation。"
        "calendar_relation 只传绝对目标到 target，参考时间由运行时自动提供；"
        "例如 target=2026-07-30T03:59:00+08:00，禁止把 $now 当 target。"
        "日期差、时间加减、星期、闰年和时区换算也使用此工具。"
        "日期时间使用 ISO 8601；相对当前时刻只能传 $now 或 $today，由运行时可信时间解析。"
        "calendar_relation 按指定时区的本地日历日比较，不按是否相差 24 小时判断。"
        "涉及本地时间必须提供 IANA 时区或 UTC offset；夏令时重复时间需提供 fold。"
        "输入不足或工具失败时先澄清，不要猜测日期、时区或换算结果。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "difference",
                    "add",
                    "subtract",
                    "weekday",
                    "is_leap_year",
                    "convert_timezone",
                    "calendar_relation",
                ],
            },
            "start": {"type": "string"},
            "end": {"type": "string"},
            "datetime": {"type": "string"},
            "date": {"type": "string"},
            "target": {
                "type": "string",
                "description": (
                    "仅供 calendar_relation：要判断的绝对目标日期或日期时间。"
                    "参考时间是运行时可信的当前时间，无需也禁止传 $now。"
                ),
            },
            "year": {"type": "integer"},
            "timezone": {"type": "string"},
            "target_timezone": {"type": "string"},
            "amount": {"type": "integer"},
            "unit": {
                "type": "string",
                "enum": ["minute", "hour", "day", "week", "month", "year"],
            },
            "fold": {"type": "integer", "enum": [0, 1]},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            payload = self._invoke(args)
        except _DateTimeInputError as exc:
            return ToolResult(
                text=f"日期时间计算失败：{exc}",
                is_error=True,
                meta={"error_kind": exc.kind},
            )
        except (OverflowError, ValueError, TypeError) as exc:
            return ToolResult(
                text=f"日期时间计算失败：{exc}",
                is_error=True,
                meta={"error_kind": "invalid_datetime"},
            )
        return ToolResult(
            text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            meta={"operation": args.get("operation")},
        )

    def _invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        operation = args.get("operation")
        if operation == "is_leap_year":
            year = args.get("year")
            if isinstance(year, bool) or not isinstance(year, int):
                raise _DateTimeInputError("invalid_datetime", "year 必须是整数")
            return {"operation": operation, "year": year, "is_leap_year": calendar.isleap(year)}

        runtime_now = _runtime_now(args)
        runtime_zone = str(args.get(_RUNTIME_TIMEZONE) or "")
        fold = args.get("fold")
        if fold not in {None, 0, 1}:
            raise _DateTimeInputError("invalid_datetime", "fold 只接受 0 或 1")

        if operation == "weekday":
            raw = args.get("date") or args.get("datetime")
            value = _parse_temporal(raw, args.get("timezone"), runtime_now, runtime_zone, fold)
            weekday = value.isoweekday()
            return {
                "operation": operation,
                "input": _iso(value),
                "iso_weekday": weekday,
                "weekday": [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ][weekday - 1],
            }

        if operation == "difference":
            start = _parse_temporal(
                args.get("start"), args.get("timezone"), runtime_now, runtime_zone, fold
            )
            end = _parse_temporal(
                args.get("end"), args.get("timezone"), runtime_now, runtime_zone, fold
            )
            if isinstance(start, datetime) != isinstance(end, datetime):
                raise _DateTimeInputError(
                    "invalid_datetime", "start 和 end 必须同为日期或同为日期时间"
                )
            if isinstance(start, datetime) and isinstance(end, datetime):
                if start.tzinfo is None or end.tzinfo is None:
                    raise _DateTimeInputError("missing_timezone", "日期时间差需要明确时区")
                delta = end.astimezone(UTC) - start.astimezone(UTC)
                seconds = delta.total_seconds()
                calendar_delta = relativedelta(end, start)
                return {
                    "operation": operation,
                    "start": _iso(start),
                    "end": _iso(end),
                    "elapsed_seconds": seconds,
                    "elapsed_days": seconds / 86400,
                    "calendar_difference": _relativedelta_payload(calendar_delta),
                }
            delta = end - start
            return {
                "operation": operation,
                "start": _iso(start),
                "end": _iso(end),
                "days": delta.days,
            }

        if operation == "calendar_relation":
            if runtime_now is None:
                raise _DateTimeInputError(
                    "invalid_datetime", "calendar_relation 需要可信当前时间基准"
                )
            if not args.get("target"):
                raise _DateTimeInputError(
                    "invalid_datetime",
                    "calendar_relation 必须把要判断的绝对日期时间放在 target；"
                    "参考时间由运行时自动提供，不要传 $now",
                )
            comparison_zone = _parse_timezone(args.get("timezone") or runtime_zone)
            target = _parse_temporal(
                args.get("target"),
                args.get("timezone") or runtime_zone,
                runtime_now,
                runtime_zone,
                fold,
            )
            reference_local = runtime_now.astimezone(comparison_zone)
            if isinstance(target, datetime):
                if target.tzinfo is None:
                    raise _DateTimeInputError(
                        "missing_timezone", "calendar_relation 的日期时间必须明确时区"
                    )
                target_local: date | datetime = target.astimezone(comparison_zone)
                elapsed_seconds: float | None = (
                    target.astimezone(UTC) - runtime_now.astimezone(UTC)
                ).total_seconds()
            else:
                target_local = target
                elapsed_seconds = None
            day_delta = (
                (target_local.date() - reference_local.date()).days
                if isinstance(target_local, datetime)
                else (target_local - reference_local.date()).days
            )
            relation = {
                -1: "yesterday",
                0: "today",
                1: "tomorrow",
            }.get(day_delta, "past" if day_delta < 0 else "future")
            payload: dict[str, Any] = {
                "operation": operation,
                "reference_local": _iso(reference_local),
                "target_local": _iso(target_local),
                "timezone": str(args.get("timezone") or runtime_zone),
                "calendar_day_delta": day_delta,
                "relation": relation,
            }
            if elapsed_seconds is not None:
                payload["elapsed_seconds"] = elapsed_seconds
            return payload

        if operation in {"add", "subtract"}:
            value = _parse_temporal(
                args.get("datetime") or args.get("date"),
                args.get("timezone"),
                runtime_now,
                runtime_zone,
                fold,
            )
            amount = args.get("amount")
            unit = args.get("unit")
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise _DateTimeInputError("invalid_datetime", "amount 必须是整数")
            if unit not in {"minute", "hour", "day", "week", "month", "year"}:
                raise _DateTimeInputError("invalid_datetime", "unit 不受支持")
            signed = amount if operation == "add" else -amount
            result = _shift(value, signed, str(unit))
            if isinstance(result, datetime) and isinstance(result.tzinfo, ZoneInfo):
                result = _validate_local(result.replace(tzinfo=None), result.tzinfo, fold)
            return {
                "operation": operation,
                "input": _iso(value),
                "amount": amount,
                "unit": unit,
                "result": _iso(result),
            }

        if operation == "convert_timezone":
            value = _parse_temporal(
                args.get("datetime"), args.get("timezone"), runtime_now, runtime_zone, fold
            )
            if not isinstance(value, datetime):
                raise _DateTimeInputError("invalid_datetime", "时区换算需要日期时间，而不是纯日期")
            if value.tzinfo is None:
                raise _DateTimeInputError("missing_timezone", "源日期时间缺少时区")
            target_zone = _parse_timezone(args.get("target_timezone"))
            result = value.astimezone(target_zone)
            return {
                "operation": operation,
                "input": _iso(value),
                "result": _iso(result),
                "target_timezone": str(args.get("target_timezone")),
                "utc_offset": _offset_text(result),
                "crossed_date": value.date() != result.date(),
            }
        raise _DateTimeInputError("invalid_arguments", f"不支持的 operation: {operation!r}")


def _runtime_now(args: dict[str, Any]) -> datetime | None:
    raw = args.get(_RUNTIME_NOW)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise _DateTimeInputError("invalid_datetime", "可信运行时时间无效") from exc
    if parsed.tzinfo is None:
        raise _DateTimeInputError("missing_timezone", "可信运行时时间缺少时区")
    return parsed


def _parse_temporal(
    raw: Any,
    zone_name: Any,
    runtime_now: datetime | None,
    runtime_zone: str,
    fold: int | None,
) -> date | datetime:
    if not isinstance(raw, str) or not raw:
        raise _DateTimeInputError("invalid_datetime", "缺少日期或日期时间")
    if raw in {"$now", "$today"}:
        if runtime_now is None:
            raise _DateTimeInputError("invalid_datetime", "当前运行时没有可信时间基准")
        zone = _parse_timezone(zone_name or runtime_zone)
        localized = runtime_now.astimezone(zone)
        return localized if raw == "$now" else localized.date()

    normalized = raw.replace("Z", "+00:00")
    if "T" not in normalized and " " not in normalized:
        try:
            return date.fromisoformat(normalized)
        except ValueError as exc:
            raise _DateTimeInputError("invalid_datetime", "日期必须使用 ISO 8601") from exc
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _DateTimeInputError("invalid_datetime", "日期时间必须使用 ISO 8601") from exc
    if parsed.tzinfo is not None:
        return parsed
    if not zone_name:
        raise _DateTimeInputError("missing_timezone", "本地日期时间必须提供 timezone")
    zone = _parse_timezone(zone_name)
    if isinstance(zone, ZoneInfo):
        return _validate_local(parsed, zone, fold)
    return parsed.replace(tzinfo=zone)


def _parse_timezone(raw: Any) -> tzinfo:
    if not isinstance(raw, str) or not raw:
        raise _DateTimeInputError("missing_timezone", "缺少 timezone")
    if raw in {"UTC", "Z", "+00:00", "-00:00"}:
        return UTC
    if raw[0:1] in {"+", "-"}:
        try:
            sign = 1 if raw[0] == "+" else -1
            hours, minutes = (int(part) for part in raw[1:].split(":"))
            if hours > 23 or minutes > 59:
                raise ValueError
            return timezone(sign * timedelta(hours=hours, minutes=minutes))
        except (ValueError, TypeError) as exc:
            raise _DateTimeInputError("invalid_datetime", "UTC offset 必须形如 +08:00") from exc
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise _DateTimeInputError("invalid_datetime", f"未知 IANA 时区: {raw}") from exc


def _validate_local(naive: datetime, zone: ZoneInfo, fold: int | None) -> datetime:
    candidates = [naive.replace(tzinfo=zone, fold=item) for item in (0, 1)]
    valid = [
        item
        for item in candidates
        if item.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive
    ]
    if not valid:
        raise _DateTimeInputError("nonexistent_local_time", "该本地时间位于夏令时跳跃的不存在区间")
    ambiguous = len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset()
    if ambiguous and fold is None:
        raise _DateTimeInputError(
            "ambiguous_local_time", "该本地时间重复出现，请提供 fold=0 或 fold=1"
        )
    return candidates[fold or 0]


def _shift(value: date | datetime, amount: int, unit: str) -> date | datetime:
    if not isinstance(value, datetime) and unit in {"minute", "hour"}:
        raise _DateTimeInputError("invalid_datetime", "纯日期不能按分钟或小时加减")
    if unit == "minute":
        if isinstance(value, datetime) and value.tzinfo is not None:
            return (value.astimezone(UTC) + timedelta(minutes=amount)).astimezone(value.tzinfo)
        return value + timedelta(minutes=amount)
    if unit == "hour":
        if isinstance(value, datetime) and value.tzinfo is not None:
            return (value.astimezone(UTC) + timedelta(hours=amount)).astimezone(value.tzinfo)
        return value + timedelta(hours=amount)
    if unit == "day":
        return cast(date | datetime, value + relativedelta(days=amount))
    if unit == "week":
        return cast(date | datetime, value + relativedelta(weeks=amount))
    if unit == "month":
        return cast(date | datetime, value + relativedelta(months=amount))
    return cast(date | datetime, value + relativedelta(years=amount))


def _relativedelta_payload(value: relativedelta) -> dict[str, int]:
    return {
        "years": value.years,
        "months": value.months,
        "days": value.days,
        "hours": value.hours,
        "minutes": value.minutes,
        "seconds": value.seconds,
    }


def _iso(value: date | datetime) -> str:
    return value.isoformat()


def _offset_text(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return ""
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"
