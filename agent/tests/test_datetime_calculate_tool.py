from __future__ import annotations

import json
from typing import Any, cast

from agent.tools.builtin.datetime_calculate import DateTimeCalculateTool


def _payload(**args: object) -> dict[str, object]:
    result = DateTimeCalculateTool().invoke(args)
    assert not result.is_error, result.text
    return cast(dict[str, Any], json.loads(result.text))


def test_calendar_month_clamps_to_last_day() -> None:
    payload = _payload(operation="add", date="2024-01-31", amount=1, unit="month")
    assert payload["result"] == "2024-02-29"


def test_difference_weekday_and_leap_year() -> None:
    assert _payload(operation="difference", start="2024-02-28", end="2024-03-01")["days"] == 2
    assert _payload(operation="weekday", date="2026-07-26")["weekday"] == "Sunday"
    assert _payload(operation="is_leap_year", year=2024)["is_leap_year"] is True


def test_timezone_conversion_crosses_date() -> None:
    payload = _payload(
        operation="convert_timezone",
        datetime="2026-07-26T01:00:00+08:00",
        target_timezone="America/Los_Angeles",
    )
    assert str(payload["result"]).startswith("2026-07-25T10:00:00")
    assert payload["crossed_date"] is True


def test_fixed_hour_addition_uses_elapsed_time_across_dst() -> None:
    payload = _payload(
        operation="add",
        datetime="2024-03-10T01:30:00",
        timezone="America/New_York",
        amount=1,
        unit="hour",
    )
    assert str(payload["result"]).startswith("2024-03-10T03:30:00-04:00")


def test_dst_gap_and_fold_are_explicit() -> None:
    gap = DateTimeCalculateTool().invoke(
        {
            "operation": "weekday",
            "datetime": "2024-03-10T02:30:00",
            "timezone": "America/New_York",
        }
    )
    ambiguous = DateTimeCalculateTool().invoke(
        {
            "operation": "weekday",
            "datetime": "2024-11-03T01:30:00",
            "timezone": "America/New_York",
        }
    )
    resolved = _payload(
        operation="weekday",
        datetime="2024-11-03T01:30:00",
        timezone="America/New_York",
        fold=1,
    )
    assert gap.meta["error_kind"] == "nonexistent_local_time"
    assert ambiguous.meta["error_kind"] == "ambiguous_local_time"
    assert str(resolved["input"]).endswith("-05:00")


def test_relative_dates_require_hidden_trusted_runtime() -> None:
    missing = DateTimeCalculateTool().invoke({"operation": "weekday", "date": "$today"})
    present = DateTimeCalculateTool().invoke(
        {
            "operation": "weekday",
            "date": "$today",
            "__agent_friend_runtime_now": "2026-07-26T16:00:00+08:00",
            "__agent_friend_runtime_timezone": "Asia/Shanghai",
        }
    )
    assert missing.is_error
    assert json.loads(present.text)["input"] == "2026-07-26"


def test_naive_datetime_requires_timezone() -> None:
    result = DateTimeCalculateTool().invoke(
        {"operation": "weekday", "datetime": "2026-07-26T10:00:00"}
    )
    assert result.is_error
    assert result.meta["error_kind"] == "missing_timezone"


def test_date_only_rejects_hour_arithmetic() -> None:
    result = DateTimeCalculateTool().invoke(
        {"operation": "add", "date": "2026-07-26", "amount": 1, "unit": "hour"}
    )
    assert result.is_error
