"""记忆搜索共享的确定性时间表达式解析。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from typing import Literal

Bias = Literal["start", "end"]

_ISO_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_N_HOUR_RE = re.compile(r"^(\d+)\s*小时前$")
_N_DAY_RE = re.compile(r"^(\d+)\s*天前$")
_N_WEEK_RE = re.compile(r"^(\d+)\s*周前$")
_N_MONTH_RE = re.compile(r"^(\d+)\s*个月前$")
_N_YEAR_RE = re.compile(r"^(\d+)\s*年前$")


def parse_time_expression(text: str, now: datetime, *, bias: Bias = "start") -> datetime:
    """把 ISO 8601 或已支持的中文时间短语解析为 aware datetime。"""
    s = text.strip()
    if not s:
        raise ValueError("时间表达式不能为空")
    if now.tzinfo is None:
        raise ValueError("now 必须是 timezone-aware datetime")

    iso_result = _try_parse_iso(s, now.tzinfo)
    if iso_result is not None:
        dt, is_date_only = iso_result
        return dt + timedelta(days=1) if is_date_only and bias == "end" else dt

    if s == "今天":
        return _day_bound(now, days=0, bias=bias)
    if s == "昨天":
        return _day_bound(now, days=-1, bias=bias)
    if s == "前天":
        return _day_bound(now, days=-2, bias=bias)
    if s == "本周":
        return _week_bound(now, weeks=0, bias=bias)
    if s == "上周":
        return _week_bound(now, weeks=-1, bias=bias)
    if s == "本月":
        return _month_bound(now, months=0, bias=bias)
    if s == "上月":
        return _month_bound(now, months=-1, bias=bias)
    if s == "今年":
        return _year_bound(now, years=0, bias=bias)
    if s == "去年":
        return _year_bound(now, years=-1, bias=bias)
    if s == "前年":
        return _year_bound(now, years=-2, bias=bias)

    match = _N_HOUR_RE.match(s)
    if match:
        return _hour_bound(now, hours_back=int(match.group(1)), bias=bias)
    match = _N_DAY_RE.match(s)
    if match:
        return _day_bound(now, days=-int(match.group(1)), bias=bias)
    match = _N_WEEK_RE.match(s)
    if match:
        return _week_bound(now, weeks=-int(match.group(1)), bias=bias)
    match = _N_MONTH_RE.match(s)
    if match:
        return _month_bound(now, months=-int(match.group(1)), bias=bias)
    match = _N_YEAR_RE.match(s)
    if match:
        return _year_bound(now, years=-int(match.group(1)), bias=bias)

    raise ValueError(f"无法识别的时间表达式：{text!r}")


def _try_parse_iso(s: str, tz: tzinfo) -> tuple[datetime, bool] | None:
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt, bool(_ISO_DATE_ONLY_RE.match(s))


def _hour_bound(now: datetime, hours_back: int, bias: Bias) -> datetime:
    target = now - timedelta(hours=hours_back)
    return target if bias == "start" else target + timedelta(hours=1)


def _day_bound(now: datetime, days: int, bias: Bias) -> datetime:
    target = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return target if bias == "start" else target + timedelta(days=1)


def _week_bound(now: datetime, weeks: int, bias: Bias) -> datetime:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target = today - timedelta(days=today.weekday()) + timedelta(weeks=weeks)
    return target if bias == "start" else target + timedelta(weeks=1)


def _month_bound(now: datetime, months: int, bias: Bias) -> datetime:
    year, month = _shift_month(now.year, now.month, months)
    start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    if bias == "start":
        return start
    next_year, next_month = _shift_month(year, month, 1)
    return start.replace(year=next_year, month=next_month)


def _year_bound(now: datetime, years: int, bias: Bias) -> datetime:
    target_year = now.year + years
    start = now.replace(year=target_year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return start if bias == "start" else start.replace(year=target_year + 1)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1
