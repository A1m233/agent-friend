"""WeatherQueryTool 的意图、预警与信任边界测试。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, cast

from agent.tools.builtin.weather_query import (
    AlertCheck,
    CurrentWeather,
    DailyForecast,
    DailyForecastResult,
    HourlyForecast,
    HourlyForecastResult,
    WeatherAlert,
    WeatherQueryTool,
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
    assert WeatherQueryTool.execution_kind == "parallel_read"
    assert WeatherQueryTool.timeout_seconds == 30.0
