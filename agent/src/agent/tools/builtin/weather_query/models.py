"""天气查询的 provider-agnostic 领域模型与错误。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar, Literal, NoReturn, Protocol, runtime_checkable

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
    """天气 provider 错误基类，携带可安全持久化的结构化诊断。"""

    error_kind: ClassVar[str] = "provider"
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str = "",
        *,
        provider_operation: str | None = None,
        status_code: int | None = None,
        provider_code: str | None = None,
        request_id: str | None = None,
        exception_type: str | None = None,
        error_kind: str | None = None,
        retryable: bool | None = None,
        attempts: tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__(message)
        self.provider_operation = provider_operation
        self.status_code = status_code
        self.provider_code = provider_code
        self.request_id = request_id
        self.exception_type = exception_type
        self.resolved_error_kind = error_kind or self.error_kind
        self.is_retryable = self.retryable if retryable is None else retryable
        self.attempts = tuple(dict(item) for item in attempts)

    def diagnostic_meta(self) -> dict[str, Any]:
        """返回不含 URL、查询参数、凭据和 provider 原始消息的诊断字段。"""

        meta: dict[str, Any] = {
            "error_kind": self.resolved_error_kind,
            "exception_type": self.exception_type or type(self).__name__,
            "retryable": self.is_retryable,
        }
        if self.provider_operation:
            meta["provider_operation"] = self.provider_operation
        if self.status_code is not None:
            meta["status_code"] = self.status_code
        if self.provider_code:
            meta["provider_code"] = self.provider_code
        if self.request_id:
            meta["request_id"] = self.request_id
        if self.attempts:
            meta["attempt_count"] = len(self.attempts)
            meta["retries_taken"] = max(0, len(self.attempts) - 1)
            meta["attempts"] = [dict(item) for item in self.attempts]
        return meta

    def with_attempts(self, attempts: tuple[dict[str, Any], ...]) -> WeatherError:
        """复制当前错误并附加 provider 已执行的安全 attempt 摘要。"""

        return self.__class__(
            str(self),
            provider_operation=self.provider_operation,
            status_code=self.status_code,
            provider_code=self.provider_code,
            request_id=self.request_id,
            exception_type=self.exception_type,
            error_kind=self.resolved_error_kind,
            retryable=self.is_retryable,
            attempts=attempts,
        )


class WeatherAuthenticationError(WeatherError):
    """凭据缺失、无效、Host 非法或服务权限不足。"""

    error_kind = "auth"


class WeatherQuotaError(WeatherError):
    """调用额度耗尽。"""

    error_kind = "quota"


class WeatherRateLimitError(WeatherError):
    """请求频率受限。"""

    error_kind = "rate_limit"


class WeatherInvalidRequestError(WeatherError):
    """参数或地点被 provider 拒绝。"""

    error_kind = "invalid_request"


class WeatherTemporaryError(WeatherError):
    """网络、上游服务或响应结构临时异常。"""

    error_kind = "temporary"
    retryable = True


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
