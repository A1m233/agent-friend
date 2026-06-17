"""``time_parser.parse_time_expression`` 单元测试。

固定 ``now`` 为 ``2026-06-17 14:30:00+08:00``（**周三**）覆盖所有短语 +
双 bias + 边界 + 失败 case。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.2。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from agent.tools.builtin.conversation_history.time_parser import parse_time_expression

# CST = UTC+8；选周三便于覆盖"本周一在过去"的语义
_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 6, 17, 14, 30, 0, tzinfo=_TZ)


def _dt(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=_TZ)


# ===== ISO 8601 =====


def test_iso_datetime_with_tz_bias_no_effect() -> None:
    iso = "2026-06-15T14:00:00+08:00"
    expected = _dt(2026, 6, 15, 14, 0)
    assert parse_time_expression(iso, _NOW, bias="start") == expected
    assert parse_time_expression(iso, _NOW, bias="end") == expected


def test_iso_datetime_no_tz_uses_now_tz() -> None:
    """ISO datetime 不带时区时按 now 时区补齐。"""
    iso = "2026-06-15T14:00:00"
    expected = _dt(2026, 6, 15, 14, 0)
    assert parse_time_expression(iso, _NOW, bias="start") == expected


def test_iso_date_only_start_returns_midnight() -> None:
    assert parse_time_expression("2026-06-15", _NOW, bias="start") == _dt(2026, 6, 15)


def test_iso_date_only_end_returns_next_midnight() -> None:
    assert parse_time_expression("2026-06-15", _NOW, bias="end") == _dt(2026, 6, 16)


# ===== 日级特殊词 =====


def test_today_start() -> None:
    assert parse_time_expression("今天", _NOW, bias="start") == _dt(2026, 6, 17)


def test_today_end() -> None:
    assert parse_time_expression("今天", _NOW, bias="end") == _dt(2026, 6, 18)


def test_yesterday() -> None:
    assert parse_time_expression("昨天", _NOW, bias="start") == _dt(2026, 6, 16)
    assert parse_time_expression("昨天", _NOW, bias="end") == _dt(2026, 6, 17)


def test_day_before_yesterday() -> None:
    assert parse_time_expression("前天", _NOW, bias="start") == _dt(2026, 6, 15)
    assert parse_time_expression("前天", _NOW, bias="end") == _dt(2026, 6, 16)


def test_n_days_ago() -> None:
    assert parse_time_expression("3 天前", _NOW, bias="start") == _dt(2026, 6, 14)
    assert parse_time_expression("3 天前", _NOW, bias="end") == _dt(2026, 6, 15)
    # 紧凑写法（无空格）也支持
    assert parse_time_expression("3天前", _NOW, bias="start") == _dt(2026, 6, 14)


def test_n_days_ago_zero_equals_today() -> None:
    assert parse_time_expression("0 天前", _NOW, bias="start") == _dt(2026, 6, 17)


# ===== 周级 =====


def test_this_week_starts_at_monday() -> None:
    """now 是周三（2026-06-17），本周一是 2026-06-15。"""
    assert parse_time_expression("本周", _NOW, bias="start") == _dt(2026, 6, 15)
    assert parse_time_expression("本周", _NOW, bias="end") == _dt(2026, 6, 22)


def test_last_week() -> None:
    assert parse_time_expression("上周", _NOW, bias="start") == _dt(2026, 6, 8)
    assert parse_time_expression("上周", _NOW, bias="end") == _dt(2026, 6, 15)


def test_n_weeks_ago() -> None:
    assert parse_time_expression("2 周前", _NOW, bias="start") == _dt(2026, 6, 1)
    assert parse_time_expression("2 周前", _NOW, bias="end") == _dt(2026, 6, 8)


# ===== 月级 =====


def test_this_month() -> None:
    assert parse_time_expression("本月", _NOW, bias="start") == _dt(2026, 6, 1)
    assert parse_time_expression("本月", _NOW, bias="end") == _dt(2026, 7, 1)


def test_last_month() -> None:
    assert parse_time_expression("上月", _NOW, bias="start") == _dt(2026, 5, 1)
    assert parse_time_expression("上月", _NOW, bias="end") == _dt(2026, 6, 1)


def test_n_months_ago() -> None:
    assert parse_time_expression("2 个月前", _NOW, bias="start") == _dt(2026, 4, 1)
    assert parse_time_expression("2 个月前", _NOW, bias="end") == _dt(2026, 5, 1)


def test_n_months_ago_cross_year() -> None:
    """``now=2026-06-17`` + ``8 个月前`` → 2025-10-01。"""
    assert parse_time_expression("8 个月前", _NOW, bias="start") == _dt(2025, 10, 1)
    assert parse_time_expression("8 个月前", _NOW, bias="end") == _dt(2025, 11, 1)


def test_last_month_in_january_crosses_year() -> None:
    jan_now = datetime(2026, 1, 15, 10, 0, tzinfo=_TZ)
    assert parse_time_expression("上月", jan_now, bias="start") == _dt(2025, 12, 1)
    assert parse_time_expression("上月", jan_now, bias="end") == _dt(2026, 1, 1)


# ===== 年级 =====


def test_this_year() -> None:
    assert parse_time_expression("今年", _NOW, bias="start") == _dt(2026, 1, 1)
    assert parse_time_expression("今年", _NOW, bias="end") == _dt(2027, 1, 1)


def test_last_year() -> None:
    assert parse_time_expression("去年", _NOW, bias="start") == _dt(2025, 1, 1)
    assert parse_time_expression("去年", _NOW, bias="end") == _dt(2026, 1, 1)


def test_year_before_last() -> None:
    assert parse_time_expression("前年", _NOW, bias="start") == _dt(2024, 1, 1)
    assert parse_time_expression("前年", _NOW, bias="end") == _dt(2025, 1, 1)


def test_n_years_ago() -> None:
    assert parse_time_expression("5 年前", _NOW, bias="start") == _dt(2021, 1, 1)
    assert parse_time_expression("5 年前", _NOW, bias="end") == _dt(2022, 1, 1)


# ===== 小时级 =====


def test_n_hours_ago_is_precise_window() -> None:
    """``2 小时前`` 作为一小时窗：start = now - 2h，end = now - 1h。"""
    assert parse_time_expression("2 小时前", _NOW, bias="start") == _NOW - timedelta(hours=2)
    assert parse_time_expression("2 小时前", _NOW, bias="end") == _NOW - timedelta(hours=1)


# ===== 边界 / 失败 =====


def test_strip_whitespace() -> None:
    assert parse_time_expression("  今天  ", _NOW, bias="start") == _dt(2026, 6, 17)


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        parse_time_expression("", _NOW)
    with pytest.raises(ValueError):
        parse_time_expression("   ", _NOW)


def test_naive_now_raises() -> None:
    naive = datetime(2026, 6, 17, 14, 30)
    with pytest.raises(ValueError):
        parse_time_expression("今天", naive)


def test_unknown_expression_raises() -> None:
    with pytest.raises(ValueError):
        parse_time_expression("明天", _NOW)  # 本期不支持未来
    with pytest.raises(ValueError):
        parse_time_expression("abc", _NOW)
    with pytest.raises(ValueError):
        parse_time_expression("-3 天前", _NOW)  # 负数不接受


def test_n_zero_year_equals_this_year() -> None:
    """``0 年前`` 退化为「今年」语义。"""
    assert parse_time_expression("0 年前", _NOW, bias="start") == _dt(2026, 1, 1)
