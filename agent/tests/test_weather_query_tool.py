"""WeatherQueryTool 的意图、预警与信任边界测试。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, cast

import pytest
from agent.tools.builtin.weather_query import (
    AlertCheck,
    CurrentWeather,
    DailyForecast,
    DailyForecastResult,
    HourlyForecast,
    HourlyForecastResult,
    WeatherAlert,
    WeatherQueryTool,
    WeatherRateLimitError,
    WeatherTemporaryError,
)
from agent.tools.location import LocationCandidate

NOW = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)


def _location(ref: str = "qweather-city:1", name: str = "Beijing") -> LocationCandidate:
    return LocationCandidate(
        ref=ref,
        name=name,
        address=f"China Beijing {name}",
        city="Beijing",
        district=name,
        longitude=116.41,
        latitude=39.92,
        country="China",
        country_code="cn",
        timezone="Asia/Shanghai",
    )


class _Resolver:
    name: ClassVar[str] = "stub"

    def __init__(self, candidates: tuple[LocationCandidate, ...]) -> None:
        self.candidates = candidates

    def search_locations(
        self, *, query: str, administrative_area: str | None
    ) -> tuple[LocationCandidate, ...]:
        return self.candidates

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        return next((item for item in self.candidates if item.ref == ref), None)


class _FailingResolver:
    name: ClassVar[str] = "failing"

    def search_locations(
        self, *, query: str, administrative_area: str | None
    ) -> tuple[LocationCandidate, ...]:
        raise WeatherTemporaryError(
            "secret upstream detail",
            provider_operation="location_lookup",
            exception_type="ReadTimeout",
            error_kind="timeout",
            attempts=(
                {
                    "attempt": 1,
                    "duration_seconds": 5.0,
                    "outcome": "error",
                    "error_kind": "timeout",
                    "exception_type": "ReadTimeout",
                },
                {
                    "attempt": 2,
                    "duration_seconds": 5.0,
                    "outcome": "error",
                    "error_kind": "timeout",
                    "exception_type": "ReadTimeout",
                },
            ),
        )

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        return None


class _RateLimitedResolver:
    name: ClassVar[str] = "rate-limited"

    def search_locations(
        self, *, query: str, administrative_area: str | None
    ) -> tuple[LocationCandidate, ...]:
        raise WeatherRateLimitError("secret upstream rate detail")

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        return None


class _Provider:
    name: ClassVar[str] = "fake-weather"

    def __init__(self, *, alert_error: bool = False, alerts: tuple[WeatherAlert, ...] = ()) -> None:
        self.calls: list[str] = []
        self.alert_error = alert_error
        self.alerts = alerts

    def get_current(self, location: LocationCandidate, *, units: str) -> CurrentWeather:
        self.calls.append("current")
        return CurrentWeather(NOW - timedelta(minutes=10), "Cloudy", 30, 33, 70)

    def get_hourly(
        self, location: LocationCandidate, *, hours: int, units: str
    ) -> HourlyForecastResult:
        self.calls.append(f"hourly:{hours}")
        return HourlyForecastResult(
            NOW - timedelta(hours=1),
            tuple(
                HourlyForecast(NOW + timedelta(hours=index), "Rain", 28) for index in range(hours)
            ),
        )

    def get_daily(
        self, location: LocationCandidate, *, days: int, units: str
    ) -> DailyForecastResult:
        self.calls.append(f"daily:{days}")
        return DailyForecastResult(
            NOW - timedelta(hours=2),
            tuple(
                DailyForecast(date(2026, 7, 28 + index), "Cloudy", "Rain", 25, 32)
                for index in range(days)
            ),
        )

    def get_alerts(self, location: LocationCandidate) -> AlertCheck:
        self.calls.append("alerts")
        if self.alert_error:
            raise WeatherTemporaryError("secret upstream detail")
        return AlertCheck(
            "active" if self.alerts else "no_active_alerts",
            NOW,
            self.alerts,
        )


def _payload(tool: WeatherQueryTool, args: dict[str, object]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(tool.invoke(args).text))


def test_overview_fetches_all_weather_segments_and_alerts() -> None:
    provider = _Provider()
    result = WeatherQueryTool(
        resolver=_Resolver((_location(),)), provider=provider, clock=lambda: NOW
    ).invoke({"location": "Beijing"})

    payload = json.loads(result.text)
    assert result.is_error is False
    assert payload["status"] == "success"
    assert len(payload["hourly"]) == 24
    assert len(payload["daily"]) == 3
    assert set(provider.calls) == {"current", "hourly:24", "daily:3", "alerts"}
    assert payload["alert_check"]["status"] == "no_active_alerts"
    assert result.meta["weather_segment_count"] == 3


def test_each_weather_view_still_checks_alerts() -> None:
    for view, expected in (("current", "current"), ("hourly", "hourly:6"), ("daily", "daily:2")):
        provider = _Provider()
        tool = WeatherQueryTool(
            resolver=_Resolver((_location(),)), provider=provider, clock=lambda: NOW
        )
        tool.invoke({"location": "Beijing", "view": view, "hours": 6, "days": 2})
        assert set(provider.calls) == {expected, "alerts"}


def test_ambiguous_location_requires_clarification_without_weather_calls() -> None:
    provider = _Provider()
    payload = _payload(
        WeatherQueryTool(
            resolver=_Resolver((_location("a", "Springfield A"), _location("b", "Springfield B"))),
            provider=provider,
            clock=lambda: NOW,
        ),
        {"location": "Springfield"},
    )

    assert payload["status"] == "needs_clarification"
    assert [item["location_ref"] for item in payload["candidates"]] == ["a", "b"]
    assert provider.calls == []


@pytest.mark.parametrize(
    "args",
    [
        {"location": "深圳南山区", "view": "current"},
        {
            "location": "南山区",
            "administrative_area": "深圳市",
            "view": "current",
        },
    ],
)
def test_unique_administrative_hierarchy_selects_matching_district(
    args: dict[str, object],
) -> None:
    shenzhen = LocationCandidate(
        ref="qweather-city:101280604",
        name="南山",
        address="中国 广东省 深圳 南山",
        city="深圳",
        district="南山",
        longitude=113.93,
        latitude=22.53,
        country="中国",
        country_code="cn",
        timezone="Asia/Shanghai",
    )
    hegang = LocationCandidate(
        ref="qweather-city:101051104",
        name="南山",
        address="中国 黑龙江省 鹤岗 南山",
        city="鹤岗",
        district="南山",
        longitude=130.28,
        latitude=47.31,
        country="中国",
        country_code="cn",
        timezone="Asia/Shanghai",
    )
    provider = _Provider()
    result = WeatherQueryTool(
        resolver=_Resolver((shenzhen, hegang)),
        provider=provider,
        clock=lambda: NOW,
    ).invoke(args)

    payload = json.loads(result.text)
    assert payload["status"] == "success"
    assert payload["location"]["name"] == "南山"
    assert payload["location"]["city"] == "深圳"
    assert set(provider.calls) == {"current", "alerts"}


def test_district_without_hierarchy_still_requires_clarification() -> None:
    candidates = (
        LocationCandidate(
            "shenzhen-nanshan",
            "南山",
            "中国 广东省 深圳 南山",
            "深圳",
            "南山",
            113.93,
            22.53,
            "中国",
            "cn",
            "Asia/Shanghai",
        ),
        LocationCandidate(
            "hegang-nanshan",
            "南山",
            "中国 黑龙江省 鹤岗 南山",
            "鹤岗",
            "南山",
            130.28,
            47.31,
            "中国",
            "cn",
            "Asia/Shanghai",
        ),
    )
    provider = _Provider()
    payload = _payload(
        WeatherQueryTool(
            resolver=_Resolver(candidates),
            provider=provider,
            clock=lambda: NOW,
        ),
        {"location": "南山区", "view": "current"},
    )

    assert payload["status"] == "needs_clarification"
    assert provider.calls == []


def test_alert_failure_is_partial_and_never_no_alerts() -> None:
    provider = _Provider(alert_error=True)
    result = WeatherQueryTool(
        resolver=_Resolver((_location(),)), provider=provider, clock=lambda: NOW
    ).invoke({"location": "Beijing", "view": "current"})

    payload = json.loads(result.text)
    assert result.is_error is False
    assert payload["status"] == "partial_success"
    assert payload["alert_check"]["status"] == "failed"
    assert "不能据此判断为没有预警" in payload["alert_check"]["message"]
    assert "secret upstream detail" not in result.text
    assert result.meta["segment_failures"]["alerts"] == {
        "error_kind": "temporary",
        "exception_type": "WeatherTemporaryError",
        "retryable": True,
    }
    assert "secret upstream detail" not in json.dumps(result.meta, ensure_ascii=False)


def test_location_failure_persists_safe_retry_diagnostics_in_tool_meta() -> None:
    result = WeatherQueryTool(
        resolver=_FailingResolver(),
        provider=_Provider(),
        clock=lambda: NOW,
    ).invoke({"location": "深圳南山区", "view": "overview"})

    payload = json.loads(result.text)
    assert result.is_error is True
    assert payload["status"] == "temporary_failure"
    assert payload["retry_mode"] == "user_trigger_required"
    assert "由用户稍后重新发起查询" in payload["next_action"]
    assert "请稍后再试" not in payload["message"]
    assert result.meta["provider"] == "fake-weather"
    assert result.meta["failure_stage"] == "location_resolution"
    assert result.meta["provider_operation"] == "location_lookup"
    assert result.meta["error_kind"] == "timeout"
    assert result.meta["exception_type"] == "ReadTimeout"
    assert result.meta["attempt_count"] == 2
    assert result.meta["retries_taken"] == 1
    assert "secret upstream detail" not in result.text
    assert "secret upstream detail" not in json.dumps(result.meta, ensure_ascii=False)


def test_rate_limit_failure_requires_user_to_retrigger_without_retry_promise() -> None:
    result = WeatherQueryTool(
        resolver=_RateLimitedResolver(),
        provider=_Provider(),
        clock=lambda: NOW,
    ).invoke({"location": "深圳南山区", "view": "overview"})

    payload = json.loads(result.text)
    assert result.is_error is True
    assert payload["status"] == "rate_limited"
    assert payload["retry_mode"] == "user_trigger_required"
    assert "由用户稍后重新发起查询" in payload["next_action"]
    assert "请稍后再试" not in payload["message"]
    assert "secret upstream rate detail" not in result.text


def test_cancel_and_expired_alerts_are_not_reported_active() -> None:
    active = WeatherAlert(
        "old", "alert", (), "Storm", severity="severe", expires_at=NOW + timedelta(hours=2)
    )
    cancel = WeatherAlert(
        "cancel", "cancel", ("old",), "Storm cancelled", expires_at=NOW + timedelta(hours=1)
    )
    expired = WeatherAlert("expired", "alert", (), "Heat", expires_at=NOW - timedelta(minutes=1))
    provider = _Provider(alerts=(active, cancel, expired))
    payload = _payload(
        WeatherQueryTool(resolver=_Resolver((_location(),)), provider=provider, clock=lambda: NOW),
        {"location": "Beijing", "view": "alerts"},
    )

    assert payload["alert_check"]["status"] == "no_active_alerts"
    assert payload["alert_check"]["alerts"] == []


def test_missing_alert_expiry_is_stale_not_active() -> None:
    provider = _Provider(alerts=(WeatherAlert("x", "alert", (), "Unknown lifetime"),))
    payload = _payload(
        WeatherQueryTool(resolver=_Resolver((_location(),)), provider=provider, clock=lambda: NOW),
        {"location": "Beijing", "view": "alerts"},
    )
    assert payload["alert_check"]["status"] == "stale"
    assert "不能确认" in payload["alert_check"]["message"]


def test_description_forbids_location_guess_and_web_fallback() -> None:
    description = WeatherQueryTool.description
    assert "不得根据 IP" in description
    assert "needs_clarification" in description
    assert "不能说『没有预警』" in description
    assert "web_search" in description
    assert "不要再并行或追加 alerts 调用" in description
    assert "不会后台自动重试" in description
    assert "需要用户重新发起" in description
    assert WeatherQueryTool.execution_kind == "parallel_read"
    assert WeatherQueryTool.timeout_seconds == 30.0
