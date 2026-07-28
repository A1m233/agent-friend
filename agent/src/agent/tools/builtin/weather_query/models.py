"""天气查询的 provider-agnostic 领域模型与错误。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar, Literal, NoReturn, Protocol, runtime_checkable

from ...errors import ToolError
from ...location import LocationCandidate

UnitSystem = Literal["metric", "imperial"]
WeatherView = Literal["overview", "current", "hourly", "daily", "alerts"]
AlertCheckStatus = Literal[
    "active",
    "no_active_alerts",
    "unsupported",
    "failed",
    "stale",
]


@dataclass(frozen=True)
class CurrentWeather:
    observed_at: datetime
    condition_text: str
    temperature: float | None = None
    feels_like: float | None = None
    humidity_percent: int | None = None
    precipitation: float | None = None
    wind_direction: str | None = None
    wind_speed: float | None = None
    wind_gust: float | None = None
    visibility: float | None = None


@dataclass(frozen=True)
class HourlyForecast:
    forecast_at: datetime
    condition_text: str
    temperature: float | None = None
    precipitation_probability_percent: int | None = None
    precipitation: float | None = None
    wind_speed: float | None = None
    wind_gust: float | None = None


@dataclass(frozen=True)
class HourlyForecastResult:
    updated_at: datetime
    items: tuple[HourlyForecast, ...]


@dataclass(frozen=True)
class DailyForecast:
    local_date: date
    day_condition_text: str | None = None
    night_condition_text: str | None = None
    temperature_min: float | None = None
    temperature_max: float | None = None
    precipitation_probability_percent: int | None = None
    wind_speed_max: float | None = None


@dataclass(frozen=True)
class DailyForecastResult:
    updated_at: datetime
    items: tuple[DailyForecast, ...]


@dataclass(frozen=True)
class WeatherAlert:
    alert_id: str
    message_type: str
    supersedes: tuple[str, ...]
    event_name: str
    sender_name: str | None = None
    event_code: str | None = None
    headline: str | None = None
    severity: str | None = None
    urgency: str | None = None
    certainty: str | None = None
    issued_at: datetime | None = None
    effective_at: datetime | None = None
    onset_at: datetime | None = None
    expires_at: datetime | None = None
    affected_area: str | None = None
    description: str | None = None
    instruction: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class AlertCheck:
    status: AlertCheckStatus
    checked_at: datetime
    alerts: tuple[WeatherAlert, ...] = ()
    message: str | None = None


class WeatherError(ToolError):
    """天气 provider 错误基类。"""


class WeatherAuthenticationError(WeatherError):
    """凭据缺失、无效、Host 非法或服务权限不足。"""


class WeatherQuotaError(WeatherError):
    """调用额度耗尽。"""


class WeatherRateLimitError(WeatherError):
    """请求频率受限。"""


class WeatherInvalidRequestError(WeatherError):
    """参数或地点被 provider 拒绝。"""


class WeatherTemporaryError(WeatherError):
    """网络、上游服务或响应结构临时异常。"""


@runtime_checkable
class WeatherProvider(Protocol):
    name: ClassVar[str]

    def get_current(self, location: LocationCandidate, *, units: UnitSystem) -> CurrentWeather: ...

    def get_hourly(
        self, location: LocationCandidate, *, hours: int, units: UnitSystem
    ) -> HourlyForecastResult: ...

    def get_daily(
        self, location: LocationCandidate, *, days: int, units: UnitSystem
    ) -> DailyForecastResult: ...

    def get_alerts(self, location: LocationCandidate) -> AlertCheck: ...


class UnavailableWeatherProvider:
    """未配置天气凭据时保留工具表面。"""

    name: ClassVar[str] = "unconfigured"

    def _raise(self) -> NoReturn:
        raise WeatherAuthenticationError("天气服务凭据未配置")

    def get_current(self, location: LocationCandidate, *, units: UnitSystem) -> CurrentWeather:
        self._raise()

    def get_hourly(
        self, location: LocationCandidate, *, hours: int, units: UnitSystem
    ) -> HourlyForecastResult:
        self._raise()

    def get_daily(
        self, location: LocationCandidate, *, days: int, units: UnitSystem
    ) -> DailyForecastResult:
        self._raise()

    def get_alerts(self, location: LocationCandidate) -> AlertCheck:
        self._raise()


class UnavailableWeatherLocationResolver:
    """天气未配置时阻止地点解析先发起其他网络请求。"""

    name: ClassVar[str] = "unconfigured"

    def search_locations(
        self,
        *,
        query: str,
        administrative_area: str | None,
    ) -> tuple[LocationCandidate, ...]:
        raise WeatherAuthenticationError("天气服务凭据未配置")

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        raise WeatherAuthenticationError("天气服务凭据未配置")
