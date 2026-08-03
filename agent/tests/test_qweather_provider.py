"""QWeather adapter 的请求、解析与安全测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from agent.tools.builtin.weather_query import (
    WeatherAuthenticationError,
    WeatherTemporaryError,
)
from agent.tools.builtin.weather_query.providers import QWeatherProvider
from agent.tools.location import LocationCandidate

NOW = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _response(request: httpx.Request, payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=request, content=json.dumps(payload).encode())


def _location(*, country: str = "China", code: str | None = "cn") -> LocationCandidate:
    return LocationCandidate(
        "qweather-city:1",
        "Dongcheng",
        "China Beijing Dongcheng",
        "Beijing",
        "Dongcheng",
        116.42,
        39.92,
        country,
        code,
        "Asia/Shanghai",
    )


@pytest.mark.parametrize(
    "host",
    [
        "",
        "https://abc.qweatherapi.com",
        "qweatherapi.com",
        "evil.example",
        "abc.qweatherapi.com/path",
        "abc.qweatherapi.com:443",
    ],
)
def test_rejects_non_account_api_hosts(host: str) -> None:
    with pytest.raises(WeatherAuthenticationError):
        QWeatherProvider(api_key="key", api_host=host)


def test_city_lookup_uses_header_and_returns_timezone_country_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "abc.qweatherapi.com"
        assert request.headers["X-QW-Api-Key"] == "secret"
        assert "secret" not in str(request.url)
        return _response(
            request,
            {
                "code": "200",
                "location": [
                    {
                        "name": "Dongcheng",
                        "id": "101011600",
                        "lat": "39.91755",
                        "lon": "116.41876",
                        "adm2": "Beijing",
                        "adm1": "Beijing",
                        "country": "China",
                        "tz": "Asia/Shanghai",
                    }
                ],
            },
        )

    with _client(handler) as client:
        provider = QWeatherProvider(api_key="secret", api_host="abc.qweatherapi.com", client=client)
        candidates = provider.search_locations(query="Dongcheng", administrative_area="Beijing")

    assert len(candidates) == 1
    assert candidates[0].ref == "qweather-city:101011600"
    assert candidates[0].country_code == "cn"
    assert candidates[0].timezone == "Asia/Shanghai"


def test_country_administrative_area_uses_iso_range_instead_of_adm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["range"] == "cn"
        assert "adm" not in request.url.params
        return _response(
            request,
            {
                "code": "200",
                "location": [
                    {
                        "name": "Beijing",
                        "id": "101010100",
                        "lat": "39.90499",
                        "lon": "116.40529",
                        "adm2": "Beijing",
                        "adm1": "Beijing",
                        "country": "China",
                        "tz": "Asia/Shanghai",
                    }
                ],
            },
        )

    with _client(handler) as client:
        candidates = QWeatherProvider(
            api_key="secret", api_host="abc.qweatherapi.com", client=client
        ).search_locations(query="Beijing", administrative_area="China")

    assert candidates[0].address == "China Beijing"


def test_non_country_administrative_area_still_uses_adm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["adm"] == "Beijing"
        assert "range" not in request.url.params
        return _response(
            request,
            {
                "code": "200",
                "location": [
                    {
                        "name": "Dongcheng",
                        "id": "101011600",
                        "lat": "39.91755",
                        "lon": "116.41876",
                        "adm2": "Beijing",
                        "adm1": "Beijing",
                        "country": "China",
                        "tz": "Asia/Shanghai",
                    }
                ],
            },
        )

    with _client(handler) as client:
        QWeatherProvider(
            api_key="secret", api_host="abc.qweatherapi.com", client=client
        ).search_locations(query="Dongcheng", administrative_area="Beijing")


def test_chinese_query_requests_chinese_location_names() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["lang"] == "zh"
        return _response(
            request,
            {
                "code": "200",
                "location": [
                    {
                        "name": "北京",
                        "id": "101010100",
                        "lat": "39.90499",
                        "lon": "116.40529",
                        "adm2": "北京",
                        "adm1": "北京市",
                        "country": "中国",
                        "tz": "Asia/Shanghai",
                    }
                ],
            },
        )

    with _client(handler) as client:
        candidates = QWeatherProvider(
            api_key="secret", api_host="abc.qweatherapi.com", client=client
        ).search_locations(query="北京", administrative_area=None)

    assert candidates[0].name == "北京"
    assert candidates[0].country_code == "cn"


def test_current_weather_parses_units_and_observation_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v7/weather/now"
        assert request.url.params["unit"] == "m"
        return _response(
            request,
            {
                "code": "200",
                "now": {
                    "obsTime": "2026-07-28T13:50+08:00",
                    "text": "多云",
                    "temp": "31",
                    "feelsLike": "34",
                    "humidity": "68",
                    "precip": "0.0",
                    "windDir": "东南风",
                    "windSpeed": "12",
                    "vis": "18",
                },
            },
        )

    with _client(handler) as client:
        current = QWeatherProvider(
            api_key="secret", api_host="abc.qweatherapi.com", client=client
        ).get_current(_location(), units="metric")

    assert current.temperature == 31
    assert current.humidity_percent == 68
    assert current.observed_at.utcoffset() is not None


def test_alert_zero_result_is_no_active_only_for_supported_country() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/weatheralert/v1/current/39.92/116.42"
        return _response(request, {"metadata": {"zeroResult": True}, "alerts": []})

    with _client(handler) as client:
        provider = QWeatherProvider(
            api_key="secret", api_host="abc.qweatherapi.com", client=client, clock=lambda: NOW
        )
        supported = provider.get_alerts(_location())
        unsupported = provider.get_alerts(_location(country="Japan", code="jp"))

    assert supported.status == "no_active_alerts"
    assert unsupported.status == "unsupported"
    assert calls == 1


def test_alert_parser_preserves_unknown_enums_and_lifecycle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {
                "metadata": {"zeroResult": False},
                "alerts": [
                    {
                        "id": "alert-1",
                        "senderName": "Authority",
                        "issuedTime": "2026-07-28T05:00Z",
                        "messageType": {"code": "update", "supersedes": ["old"]},
                        "eventType": {"name": "Future Hazard", "code": "9999"},
                        "urgency": "new-urgency",
                        "severity": "new-severity",
                        "certainty": "new-certainty",
                        "expireTime": "2026-07-28T08:00Z",
                        "headline": "Official headline",
                        "instruction": "Follow authority guidance",
                    }
                ],
            },
        )

    with _client(handler) as client:
        check = QWeatherProvider(
            api_key="secret", api_host="abc.qweatherapi.com", client=client, clock=lambda: NOW
        ).get_alerts(_location())

    assert check.status == "active"
    assert check.alerts[0].supersedes == ("old",)
    assert check.alerts[0].severity == "new-severity"


def test_alert_empty_response_without_zero_result_is_failed_not_no_active() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, {"metadata": {}, "alerts": []})

    with _client(handler) as client:
        check = QWeatherProvider(
            api_key="secret",
            api_host="abc.qweatherapi.com",
            client=client,
            clock=lambda: NOW,
        ).get_alerts(_location())

    assert check.status == "failed"
    assert "未能核实" in (check.message or "")


def test_transient_timeout_retries_once_then_recovers() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("sensitive upstream detail", request=request)
        return _response(
            request,
            {
                "code": "200",
                "location": [
                    {
                        "name": "深圳市",
                        "id": "101280601",
                        "lat": "22.55",
                        "lon": "114.05",
                        "adm2": "深圳市",
                        "adm1": "广东省",
                        "country": "中国",
                        "tz": "Asia/Shanghai",
                    }
                ],
            },
        )

    with _client(handler) as client:
        candidates = QWeatherProvider(
            api_key="secret",
            api_host="abc.qweatherapi.com",
            client=client,
            retry_delay_seconds=0.25,
            sleep=delays.append,
        ).search_locations(query="深圳", administrative_area=None)

    assert calls == 2
    assert delays == [0.25]
    assert candidates[0].name == "深圳市"


def test_final_timeout_exposes_only_safe_structured_diagnostics() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout(
            "secret=leak-me host=abc.qweatherapi.com query=深圳",
            request=request,
        )

    with _client(handler) as client:
        provider = QWeatherProvider(
            api_key="secret",
            api_host="abc.qweatherapi.com",
            client=client,
            retry_delay_seconds=0,
            sleep=lambda _delay: None,
        )
        with pytest.raises(WeatherTemporaryError) as captured:
            provider.search_locations(query="深圳", administrative_area=None)

    meta = captured.value.diagnostic_meta()
    assert calls == 2
    assert meta["provider_operation"] == "location_lookup"
    assert meta["error_kind"] == "timeout"
    assert meta["exception_type"] == "ConnectTimeout"
    assert meta["retryable"] is True
    assert meta["attempt_count"] == 2
    assert meta["retries_taken"] == 1
    assert [item["attempt"] for item in meta["attempts"]] == [1, 2]
    serialized = json.dumps(meta, ensure_ascii=False)
    assert "leak-me" not in serialized
    assert "abc.qweatherapi.com" not in serialized
    assert "深圳" not in serialized


def test_authentication_failure_is_not_retried_and_keeps_status_diagnostic() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request, {"code": "401"}, status=401)

    with _client(handler) as client:
        provider = QWeatherProvider(
            api_key="secret",
            api_host="abc.qweatherapi.com",
            client=client,
            sleep=lambda _delay: pytest.fail("auth failure must not retry"),
        )
        with pytest.raises(WeatherAuthenticationError) as captured:
            provider.search_locations(query="深圳", administrative_area=None)

    meta = captured.value.diagnostic_meta()
    assert calls == 1
    assert meta["error_kind"] == "auth"
    assert meta["status_code"] == 401
    assert meta["attempt_count"] == 1
    assert meta["retries_taken"] == 0
