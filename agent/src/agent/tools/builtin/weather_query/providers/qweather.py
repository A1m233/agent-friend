"""和风天气 Web API adapter。"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from ....location import LocationCandidate
from ..models import (
    AlertCheck,
    CurrentWeather,
    DailyForecast,
    DailyForecastResult,
    HourlyForecast,
    HourlyForecastResult,
    UnitSystem,
    WeatherAlert,
    WeatherAuthenticationError,
    WeatherInvalidRequestError,
    WeatherQuotaError,
    WeatherRateLimitError,
    WeatherTemporaryError,
)

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# QWeather Alert Info 官方覆盖表，核对日期 2026-07-28。仅属于此 adapter。
_ALERT_SUPPORTED_CODES = frozenset(
    {
        "ar",
        "at",
        "be",
        "br",
        "bg",
        "ca",
        "cn",
        "hr",
        "cy",
        "cz",
        "dk",
        "ee",
        "fi",
        "fr",
        "de",
        "gr",
        "hk",
        "hu",
        "is",
        "in",
        "id",
        "ie",
        "il",
        "it",
        "kr",
        "kw",
        "lv",
        "lt",
        "lu",
        "mo",
        "mt",
        "md",
        "me",
        "nl",
        "nz",
        "mk",
        "no",
        "pl",
        "pt",
        "ro",
        "ru",
        "sa",
        "rs",
        "sg",
        "sk",
        "si",
        "za",
        "es",
        "se",
        "ch",
        "tw",
        "th",
        "gb",
        "us",
    }
)

_COUNTRY_CODES = {
    "argentina": "ar",
    "austria": "at",
    "belgium": "be",
    "brazil": "br",
    "bulgaria": "bg",
    "canada": "ca",
    "china": "cn",
    "croatia": "hr",
    "cyprus": "cy",
    "czechia": "cz",
    "denmark": "dk",
    "estonia": "ee",
    "finland": "fi",
    "france": "fr",
    "germany": "de",
    "greece": "gr",
    "hong kong": "hk",
    "hungary": "hu",
    "iceland": "is",
    "india": "in",
    "indonesia": "id",
    "ireland": "ie",
    "israel": "il",
    "italy": "it",
    "south korea": "kr",
    "republic of korea": "kr",
    "kuwait": "kw",
    "latvia": "lv",
    "lithuania": "lt",
    "luxembourg": "lu",
    "macao": "mo",
    "macau": "mo",
    "malta": "mt",
    "moldova": "md",
    "montenegro": "me",
    "netherlands": "nl",
    "new zealand": "nz",
    "north macedonia": "mk",
    "norway": "no",
    "poland": "pl",
    "portugal": "pt",
    "romania": "ro",
    "russia": "ru",
    "russian federation": "ru",
    "saudi arabia": "sa",
    "serbia": "rs",
    "singapore": "sg",
    "slovakia": "sk",
    "slovenia": "si",
    "south africa": "za",
    "spain": "es",
    "sweden": "se",
    "switzerland": "ch",
    "taiwan": "tw",
    "thailand": "th",
    "united kingdom": "gb",
    "united states": "us",
    "united states of america": "us",
}

_LOCALIZED_COUNTRY_CODES = {
    "中国": "cn",
    "中国大陆": "cn",
    "香港": "hk",
    "澳门": "mo",
    "台湾": "tw",
    "美国": "us",
    "英国": "gb",
}


class QWeatherProvider:
    """同时实现共享地点解析和天气 provider。"""

    name: ClassVar[str] = "qweather"

    def __init__(
        self,
        *,
        api_key: str,
        api_host: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key.strip():
            raise WeatherAuthenticationError("QWEATHER_API_KEY 为空")
        host = _validate_api_host(api_host)
        self._api_key = api_key.strip()
        self._base_url = f"https://{host}"
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def search_locations(
        self,
        *,
        query: str,
        administrative_area: str | None,
    ) -> tuple[LocationCandidate, ...]:
        language = _query_language(query)
        params: dict[str, Any] = {
            "location": query,
            "number": 10,
            "lang": language,
            **_city_scope(administrative_area),
        }
        payload = self._get("/geo/v2/city/lookup", params=params)
        candidates = _parse_locations(payload, kind="city")
        if candidates:
            return candidates
        poi_params: dict[str, Any] = {
            "location": query,
            "type": "scenic",
            "number": 10,
            "lang": language,
        }
        if administrative_area and not _country_code(administrative_area):
            poi_params["city"] = administrative_area
        return _parse_locations(self._get("/geo/v2/poi/lookup", params=poi_params), kind="poi")

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        if ref.startswith("qweather-city:"):
            kind, location_id = "city", ref.removeprefix("qweather-city:")
            path = "/geo/v2/city/lookup"
            params = {"location": location_id, "number": 1, "lang": "en"}
        elif ref.startswith("qweather-poi:"):
            kind, location_id = "poi", ref.removeprefix("qweather-poi:")
            path = "/geo/v2/poi/lookup"
            params = {"location": location_id, "type": "scenic", "number": 1, "lang": "en"}
        else:
            return None
        if not location_id:
            return None
        candidates = _parse_locations(self._get(path, params=params), kind=kind)
        return candidates[0] if candidates else None

    def get_current(self, location: LocationCandidate, *, units: UnitSystem) -> CurrentWeather:
        payload = self._get(
            "/v7/weather/now",
            params={"location": _coordinate(location), "lang": "zh", "unit": _unit(units)},
        )
        raw = _mapping(payload.get("now"))
        observed_at = _datetime(raw.get("obsTime"))
        condition = _text(raw.get("text"))
        if observed_at is None or not condition:
            raise WeatherTemporaryError("当前天气缺少必要字段")
        return CurrentWeather(
            observed_at=observed_at,
            condition_text=condition,
            temperature=_number(raw.get("temp")),
            feels_like=_number(raw.get("feelsLike")),
            humidity_percent=_integer(raw.get("humidity"), minimum=0, maximum=100),
            precipitation=_number(raw.get("precip"), minimum=0),
            wind_direction=_optional_text(raw.get("windDir")),
            wind_speed=_number(raw.get("windSpeed"), minimum=0),
            wind_gust=_number(raw.get("windGust"), minimum=0),
            visibility=_number(raw.get("vis"), minimum=0),
        )

    def get_hourly(
        self, location: LocationCandidate, *, hours: int, units: UnitSystem
    ) -> HourlyForecastResult:
        horizon = 24 if hours <= 24 else 72
        payload = self._get(
            f"/v7/weather/{horizon}h",
            params={"location": _coordinate(location), "lang": "zh", "unit": _unit(units)},
        )
        updated_at = _required_update_time(payload)
        items: list[HourlyForecast] = []
        for raw in _items(payload, "hourly"):
            forecast_at = _datetime(raw.get("fxTime"))
            condition = _text(raw.get("text"))
            if forecast_at is None or not condition:
                continue
            items.append(
                HourlyForecast(
                    forecast_at=forecast_at,
                    condition_text=condition,
                    temperature=_number(raw.get("temp")),
                    precipitation_probability_percent=_integer(
                        raw.get("pop"), minimum=0, maximum=100
                    ),
                    precipitation=_number(raw.get("precip"), minimum=0),
                    wind_speed=_number(raw.get("windSpeed"), minimum=0),
                    wind_gust=_number(raw.get("windGust"), minimum=0),
                )
            )
        if not items:
            raise WeatherTemporaryError("小时预报没有可用条目")
        return HourlyForecastResult(updated_at=updated_at, items=tuple(items[:hours]))

    def get_daily(
        self, location: LocationCandidate, *, days: int, units: UnitSystem
    ) -> DailyForecastResult:
        horizon = 3 if days <= 3 else 7 if days <= 7 else 10
        payload = self._get(
            f"/v7/weather/{horizon}d",
            params={"location": _coordinate(location), "lang": "zh", "unit": _unit(units)},
        )
        updated_at = _required_update_time(payload)
        items: list[DailyForecast] = []
        for raw in _items(payload, "daily"):
            local_date = _date(raw.get("fxDate"))
            if local_date is None:
                continue
            wind_day = _number(raw.get("windSpeedDay"), minimum=0)
            wind_night = _number(raw.get("windSpeedNight"), minimum=0)
            winds = [value for value in (wind_day, wind_night) if value is not None]
            items.append(
                DailyForecast(
                    local_date=local_date,
                    day_condition_text=_optional_text(raw.get("textDay")),
                    night_condition_text=_optional_text(raw.get("textNight")),
                    temperature_min=_number(raw.get("tempMin")),
                    temperature_max=_number(raw.get("tempMax")),
                    precipitation_probability_percent=_integer(
                        raw.get("precipProbability"), minimum=0, maximum=100
                    ),
                    wind_speed_max=max(winds) if winds else None,
                )
            )
        if not items:
            raise WeatherTemporaryError("多日预报没有可用条目")
        return DailyForecastResult(updated_at=updated_at, items=tuple(items[:days]))

    def get_alerts(self, location: LocationCandidate) -> AlertCheck:
        checked_at = _aware(self._clock())
        country_code = (location.country_code or "").casefold()
        if not country_code:
            country_code = _COUNTRY_CODES.get((location.country or "").strip().casefold(), "")
        if not country_code:
            return AlertCheck(
                status="failed",
                checked_at=checked_at,
                message="无法可靠确定预警覆盖地区，本次未能核实官方天气预警。",
            )
        if country_code not in _ALERT_SUPPORTED_CODES:
            return AlertCheck(
                status="unsupported",
                checked_at=checked_at,
                message="当前天气服务未覆盖该地区的官方天气预警。",
            )
        payload = self._get(
            f"/weatheralert/v1/current/{location.latitude:.2f}/{location.longitude:.2f}",
            params={"localTime": "true", "lang": "zh"},
            expect_qweather_code=False,
        )
        alerts = tuple(
            alert for raw in _items(payload, "alerts") if (alert := _parse_alert(raw)) is not None
        )
        metadata = _mapping(payload.get("metadata"))
        zero_result = metadata.get("zeroResult") is True
        if alerts:
            return AlertCheck(status="active", checked_at=checked_at, alerts=alerts)
        if zero_result:
            return AlertCheck(status="no_active_alerts", checked_at=checked_at)
        return AlertCheck(
            status="failed",
            checked_at=checked_at,
            message="预警服务响应不足以判断当前状态，本次未能核实官方天气预警。",
        )

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        expect_qweather_code: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {"X-QW-Api-Key": self._api_key, "Accept-Encoding": "gzip"}
        try:
            if self._client is not None:
                response = self._client.get(url, params=params, headers=headers)
            else:
                with httpx.Client(
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise WeatherTemporaryError("天气服务网络请求失败") from exc
        except httpx.HTTPStatusError as exc:
            _raise_http_error(exc)
        except ValueError as exc:
            raise WeatherTemporaryError("天气服务返回无法解析的数据") from exc
        if not isinstance(payload, dict):
            raise WeatherTemporaryError("天气服务返回结构异常")
        if expect_qweather_code:
            _raise_qweather_code(payload)
        return payload


def _validate_api_host(value: str) -> str:
    raw = value.strip().casefold().rstrip(".")
    if not raw:
        raise WeatherAuthenticationError("QWEATHER_API_HOST 为空")
    parsed = urlsplit(f"//{raw}")
    if (
        parsed.hostname != raw
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise WeatherAuthenticationError("QWEATHER_API_HOST 格式非法")
    if not raw.endswith(".qweatherapi.com") or raw == "qweatherapi.com":
        raise WeatherAuthenticationError("QWEATHER_API_HOST 不是账户专属域名")
    labels = raw.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise WeatherAuthenticationError("QWEATHER_API_HOST 格式非法")
    return raw


def _parse_locations(payload: Mapping[str, Any], *, kind: str) -> tuple[LocationCandidate, ...]:
    key = "location" if kind == "city" else "poi"
    candidates: list[LocationCandidate] = []
    for raw in _items(payload, key):
        location_id = _text(raw.get("id"))
        name = _text(raw.get("name"))
        latitude = _number(raw.get("lat"), minimum=-90, maximum=90)
        longitude = _number(raw.get("lon"), minimum=-180, maximum=180)
        if not location_id or not name or latitude is None or longitude is None:
            continue
        city = _optional_text(raw.get("adm2"))
        district = name if kind == "city" and city and name != city else None
        country = _optional_text(raw.get("country"))
        address = " ".join(_unique_text((country, _optional_text(raw.get("adm1")), city, name)))
        country_code = _country_code(country)
        candidates.append(
            LocationCandidate(
                ref=f"qweather-{kind}:{location_id}",
                name=name,
                address=address or name,
                city=city,
                district=district,
                longitude=longitude,
                latitude=latitude,
                country=country,
                country_code=country_code,
                timezone=_optional_text(raw.get("tz")),
            )
        )
    return tuple(candidates)


def _city_scope(administrative_area: str | None) -> dict[str, str]:
    if not administrative_area:
        return {}
    country_code = _country_code(administrative_area)
    if country_code:
        return {"range": country_code}
    return {"adm": administrative_area}


def _country_code(value: str | None) -> str | None:
    normalized = (value or "").strip().casefold()
    if not normalized:
        return None
    if len(normalized) == 2 and normalized.isascii() and normalized.isalpha():
        return normalized
    return _COUNTRY_CODES.get(normalized) or _LOCALIZED_COUNTRY_CODES.get(normalized)


def _unique_text(values: tuple[str | None, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = (value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def _query_language(query: str) -> str:
    return "zh" if any("\u3400" <= char <= "\u9fff" for char in query) else "en"


def _parse_alert(raw: Mapping[str, Any]) -> WeatherAlert | None:
    alert_id = _text(raw.get("id"))
    event = _mapping(raw.get("eventType"))
    event_name = _text(event.get("name")) or _text(raw.get("headline"))
    if not alert_id or not event_name:
        return None
    message = _mapping(raw.get("messageType"))
    message_code = _text(message.get("code")) or _text(raw.get("messageType")) or "alert"
    supersedes_raw = message.get("supersedes")
    supersedes = (
        tuple(_text(item) for item in supersedes_raw if _text(item))
        if isinstance(supersedes_raw, list)
        else ()
    )
    return WeatherAlert(
        alert_id=alert_id,
        message_type=message_code,
        supersedes=supersedes,
        event_name=event_name,
        sender_name=_optional_text(raw.get("senderName")),
        event_code=_optional_text(event.get("code")),
        headline=_optional_text(raw.get("headline")),
        severity=_optional_text(raw.get("severity")),
        urgency=_optional_text(raw.get("urgency")),
        certainty=_optional_text(raw.get("certainty")),
        issued_at=_datetime(raw.get("issuedTime")),
        effective_at=_datetime(raw.get("effectiveTime")),
        onset_at=_datetime(raw.get("onsetTime")),
        expires_at=_datetime(raw.get("expireTime") or raw.get("expiredTime")),
        affected_area=_optional_text(raw.get("areaName") or raw.get("regionName")),
        description=_optional_text(raw.get("description")),
        instruction=_optional_text(raw.get("instruction")),
        source_url=_optional_text(raw.get("sourceUrl")),
    )


def _raise_http_error(exc: httpx.HTTPStatusError) -> None:
    status = exc.response.status_code
    if status in {401, 403}:
        raise WeatherAuthenticationError("天气服务拒绝凭据") from exc
    if status == 402:
        raise WeatherQuotaError("天气服务额度已耗尽") from exc
    if status == 429:
        raise WeatherRateLimitError("天气服务请求过于频繁") from exc
    if status in {400, 404}:
        raise WeatherInvalidRequestError("天气服务拒绝查询参数") from exc
    raise WeatherTemporaryError("天气服务 HTTP 错误") from exc


def _raise_qweather_code(payload: Mapping[str, Any]) -> None:
    code = _text(payload.get("code"))
    if code == "200":
        return
    if code == "204":
        return
    if code in {"401", "403"}:
        raise WeatherAuthenticationError("天气服务凭据无效或无权限")
    if code == "402":
        raise WeatherQuotaError("天气服务额度已耗尽")
    if code == "429":
        raise WeatherRateLimitError("天气服务请求过于频繁")
    if code in {"400", "404"}:
        raise WeatherInvalidRequestError("天气服务拒绝查询参数")
    raise WeatherTemporaryError("天气服务返回未分类错误")


def _required_update_time(payload: Mapping[str, Any]) -> datetime:
    value = _datetime(payload.get("updateTime"))
    if value is None:
        raise WeatherTemporaryError("天气预报缺少更新时间")
    return value


def _coordinate(location: LocationCandidate) -> str:
    return f"{location.longitude:.2f},{location.latitude:.2f}"


def _unit(units: UnitSystem) -> str:
    return "i" if units == "imperial" else "m"


def _items(payload: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _integer(
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    number = _number(value, minimum=minimum, maximum=maximum)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed) if parsed.tzinfo is not None else None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise WeatherTemporaryError("天气时间缺少时区")
    return value
