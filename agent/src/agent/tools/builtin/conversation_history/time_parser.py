"""时间表达式解析。

把字符串（ISO 8601 / 中文自然语言短语）解析成 ``datetime``——本期支持 16 种
常用短语 + ISO 8601。**独立函数**便于本期最简实现 + 未来升级（换 dateparser /
加更多短语只动这一处）。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.2。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from typing import Literal

Bias = Literal["start", "end"]
"""时段端点偏置：

- ``"start"``：返回时间段的**开始**（半开区间左端点，包含在内）
- ``"end"``：返回时间段的**结束**（半开区间右端点，**不**包含在内）

对精确时刻输入（ISO 8601 datetime）无影响。
"""


_ISO_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_N_HOUR_RE = re.compile(r"^(\d+)\s*小时前$")
_N_DAY_RE = re.compile(r"^(\d+)\s*天前$")
_N_WEEK_RE = re.compile(r"^(\d+)\s*周前$")
_N_MONTH_RE = re.compile(r"^(\d+)\s*个月前$")
_N_YEAR_RE = re.compile(r"^(\d+)\s*年前$")


def parse_time_expression(text: str, now: datetime, *, bias: Bias = "start") -> datetime:
    """把时间表达式解析成 timezone-aware ``datetime``。

    Args:
        text: 用户/LLM 传入的字符串。前后空白会被 strip。
        now: 当前时间，必须 **timezone-aware**——所有相对短语相对它计算；
            ISO 8601 不带时区时按 ``now`` 的时区补齐。
        bias: ``"start"`` 返回时段开始（含），``"end"`` 返回时段结束（不含）；
            对精确时刻无影响。

    Returns:
        timezone-aware ``datetime``。

    Raises:
        ValueError: 无法识别 / 数值非法 / ``now`` 无时区。
    """
    s = text.strip()
    if not s:
        raise ValueError("时间表达式不能为空")

    if now.tzinfo is None:
        raise ValueError("now 必须是 timezone-aware datetime")
    tz = now.tzinfo

    # ISO 8601 优先
    iso_result = _try_parse_iso(s, tz)
    if iso_result is not None:
        dt, is_date_only = iso_result
        if is_date_only and bias == "end":
            return dt + timedelta(days=1)
        return dt

    # 日级特殊词
    if s == "今天":
        return _day_bound(now, days=0, bias=bias)
    if s == "昨天":
        return _day_bound(now, days=-1, bias=bias)
    if s == "前天":
        return _day_bound(now, days=-2, bias=bias)

    # 周级特殊词
    if s == "本周":
        return _week_bound(now, weeks=0, bias=bias)
    if s == "上周":
        return _week_bound(now, weeks=-1, bias=bias)

    # 月级特殊词
    if s == "本月":
        return _month_bound(now, months=0, bias=bias)
    if s == "上月":
        return _month_bound(now, months=-1, bias=bias)

    # 年级特殊词
    if s == "今年":
        return _year_bound(now, years=0, bias=bias)
    if s == "去年":
        return _year_bound(now, years=-1, bias=bias)
    if s == "前年":
        return _year_bound(now, years=-2, bias=bias)

    # N + 单位前
    m = _N_HOUR_RE.match(s)
    if m:
        return _hour_bound(now, hours_back=int(m.group(1)), bias=bias)
    m = _N_DAY_RE.match(s)
    if m:
        return _day_bound(now, days=-int(m.group(1)), bias=bias)
    m = _N_WEEK_RE.match(s)
    if m:
        return _week_bound(now, weeks=-int(m.group(1)), bias=bias)
    m = _N_MONTH_RE.match(s)
    if m:
        return _month_bound(now, months=-int(m.group(1)), bias=bias)
    m = _N_YEAR_RE.match(s)
    if m:
        return _year_bound(now, years=-int(m.group(1)), bias=bias)

    raise ValueError(f"无法识别的时间表达式：{text!r}")


def _try_parse_iso(s: str, tz: tzinfo) -> tuple[datetime, bool] | None:
    """尝试按 ISO 8601 解析。

    Returns:
        ``(datetime, is_date_only)`` —— is_date_only 为 True 表示输入仅含日期（无时分）。
        解析失败返回 ``None``。
    """
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt, bool(_ISO_DATE_ONLY_RE.match(s))


def _hour_bound(now: datetime, hours_back: int, bias: Bias) -> datetime:
    """``N 小时前``：start = ``now - N 小时``；end = start + 1 小时（半开窗）。"""
    target = now - timedelta(hours=hours_back)
    return target if bias == "start" else target + timedelta(hours=1)


def _day_bound(now: datetime, days: int, bias: Bias) -> datetime:
    """日级：start = 目标日 00:00；end = 次日 00:00。``days`` 为有符号偏移。"""
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target = base + timedelta(days=days)
    return target if bias == "start" else target + timedelta(days=1)


def _week_bound(now: datetime, weeks: int, bias: Bias) -> datetime:
    """周级：start = 目标周一 00:00；end = 次周一 00:00。``weeks`` 为有符号偏移。"""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = today - timedelta(days=today.weekday())
    target_monday = this_monday + timedelta(weeks=weeks)
    return target_monday if bias == "start" else target_monday + timedelta(weeks=1)


def _month_bound(now: datetime, months: int, bias: Bias) -> datetime:
    """月级：start = 目标月 1 日 00:00；end = 次月 1 日 00:00。``months`` 为有符号偏移。"""
    year, month = _shift_month(now.year, now.month, months)
    start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    if bias == "start":
        return start
    next_year, next_month = _shift_month(year, month, 1)
    return start.replace(year=next_year, month=next_month)


def _year_bound(now: datetime, years: int, bias: Bias) -> datetime:
    """年级：start = 目标年 1 月 1 日 00:00；end = 次年 1 月 1 日 00:00。``years`` 为有符号偏移。"""
    target_year = now.year + years
    start = now.replace(year=target_year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return start if bias == "start" else start.replace(year=target_year + 1)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """把 ``(year, month)`` 偏移 ``delta`` 个月，跨年自动 carry。"""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1
