"""面向用户天气意图的单工具编排。"""

from __future__ import annotations

import json
import math
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from ...errors import ToolError
from ...location import LocationCandidate, LocationResolver
from ...protocol import ToolResult
from .models import (
    AlertCheck,
    CurrentWeather,
    DailyForecastResult,
    HourlyForecastResult,
    UnitSystem,
    WeatherAlert,
    WeatherAuthenticationError,
    WeatherError,
    WeatherInvalidRequestError,
    WeatherProvider,
    WeatherQuotaError,
    WeatherRateLimitError,
    WeatherTemporaryError,
    WeatherView,
)

_CURRENT_STALE_AFTER = timedelta(hours=2)
_FORECAST_STALE_AFTER = timedelta(hours=12)
_MAX_CANDIDATES = 5
_MAX_ALERTS = 10
_ADMIN_DIVISION_MARKERS = (
    "特别行政区",
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "自治区",
    "自治州",
    "地区",
    "新区",
    "省",
    "市",
    "区",
    "县",
    "旗",
    "盟",
)


class WeatherQueryTool:
    """解析地点并按用户意图查询天气和官方天气预警。"""

    name: ClassVar[str] = "weather_query"
    execution_kind: ClassVar[str] = "parallel_read"
    timeout_seconds: ClassVar[float] = 30.0
    description: ClassVar[str] = (
        "查询用户明确指定地点的当前天气、逐小时预报、多日预报和官方天气预警。\n\n"
        "**何时必须使用**：实时天气、几点下雨、未来几天天气、极端天气或官方预警。\n"
        "**地点边界**：用户没给可靠地点时先询问；不得根据 IP、系统时区、语言、历史聊天或相似地名猜测。"
        "status=needs_clarification 时必须让用户选择候选，再原样传回 location_ref。\n"
        "**view**：普通问题用 overview；此刻天气用 current；时段变化用 hourly；未来日期用 daily；只问预警用 alerts。"
        "所有普通天气 view 都已同步检查官方预警；一次普通天气调用已足够，不要再并行或追加 alerts 调用。\n"
        "**证据边界**：alert_check 为 failed、unsupported 或 stale 时，不能说『没有预警』；"
        "预报显示风险不等于气象部门已发布预警。web_search 可补充新闻或公告原文，但不能替代天气数值、"
        "降雨时序或官方预警状态。status=temporary_failure 或 rate_limited 表示本轮已结束且不会后台自动重试；"
        "不得把本轮失败表述成之后会自动继续执行，如仍需要天气结果，应明确需要用户重新发起。"
        "不要暴露 provider、API、鉴权、限流或内部错误码。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "用户明确提供的城市、区县、地点、地址或经纬度。",
            },
            "administrative_area": {
                "type": "string",
                "description": "用户明确提供的国家、省州或城市范围，只用于缩小候选。",
            },
            "location_ref": {
                "type": "string",
                "description": "上次澄清结果中的不透明引用，只能原样复用。",
            },
            "view": {
                "type": "string",
                "enum": ["overview", "current", "hourly", "daily", "alerts"],
                "default": "overview",
                "description": "按用户问题选择的天气视图；普通问题默认 overview。",
            },
            "hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 72,
                "default": 24,
                "description": "hourly 视图返回的小时数。",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 3,
                "description": "daily/overview 视图返回的天数。",
            },
            "unit_system": {
                "type": "string",
                "enum": ["metric", "imperial"],
                "default": "metric",
                "description": "温度、风速等单位制；默认公制。",
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        resolver: LocationResolver,
        provider: WeatherProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolver = resolver
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        validated = _validate_args(args)
        if isinstance(validated, ToolResult):
            return validated
        location_text, area, ref, view, hours, days, units = validated
        started = time.monotonic()
        try:
            location = self._resolve_location(location_text, area, ref)
        except ToolError as exc:
            return _provider_failure(
                exc,
                provider=self._provider.name,
                stage="location_resolution",
            )
        if isinstance(location, ToolResult):
            return location

        requested = _requested_segments(view)
        tasks: dict[str, Callable[[], Any]] = {
            "alerts": lambda: self._provider.get_alerts(location)
        }
        if "current" in requested:
            tasks["current"] = lambda: self._provider.get_current(location, units=units)
        if "hourly" in requested:
            tasks["hourly"] = lambda: self._provider.get_hourly(location, hours=hours, units=units)
        if "daily" in requested:
            tasks["daily"] = lambda: self._provider.get_daily(location, days=days, units=units)

        values: dict[str, Any] = {}
        failures: dict[str, ToolError] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as executor:
            futures = {executor.submit(call): name for name, call in tasks.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    values[name] = future.result()
                except ToolError as exc:
                    failures[name] = exc

        now = _aware_utc(self._clock())
        payload: dict[str, Any] = {
            "source_type": "weather_query",
            "location": _location_payload(location),
            "unit_system": units,
            "requested_view": view,
        }
        usable_weather = 0
        stale_segments: list[str] = []
        current = values.get("current")
        if isinstance(current, CurrentWeather):
            if now - _aware_utc(current.observed_at) > _CURRENT_STALE_AFTER:
                stale_segments.append("current")
            else:
                payload["current"] = _jsonable(asdict(current))
                usable_weather += 1
        hourly = values.get("hourly")
        if isinstance(hourly, HourlyForecastResult):
            if now - _aware_utc(hourly.updated_at) > _FORECAST_STALE_AFTER:
                stale_segments.append("hourly")
            else:
                payload["hourly_updated_at"] = hourly.updated_at.isoformat()
                payload["hourly"] = [_jsonable(asdict(item)) for item in hourly.items[:hours]]
                usable_weather += 1
        daily = values.get("daily")
        if isinstance(daily, DailyForecastResult):
            if now - _aware_utc(daily.updated_at) > _FORECAST_STALE_AFTER:
                stale_segments.append("daily")
            else:
                payload["daily_updated_at"] = daily.updated_at.isoformat()
                payload["daily"] = [_jsonable(asdict(item)) for item in daily.items[:days]]
                usable_weather += 1

        alert_check = values.get("alerts")
        if isinstance(alert_check, AlertCheck):
            normalized = _normalize_alert_check(alert_check, now)
        elif "alerts" in failures:
            normalized = AlertCheck(
                status="failed",
                checked_at=now,
                message="本次未能核实官方天气预警，不能据此判断为没有预警。",
            )
        else:
            normalized = AlertCheck(status="failed", checked_at=now)
        payload["alert_check"] = _alert_check_payload(normalized)

        if stale_segments:
            payload["stale_segments"] = stale_segments
        if failures:
            payload["unavailable_segments"] = sorted(failures)

        alerts_answered = view == "alerts" and normalized.status != "failed"
        if usable_weather == 0 and not alerts_answered:
            if failures:
                return _provider_failure(
                    next(iter(failures.values())),
                    provider=self._provider.name,
                    stage="weather_segments",
                    failures=failures,
                )
            return _failure(
                "stale_data",
                "天气数据时间不可信，当前不能作为最新天气回答。",
            )

        complete_weather = all(name in values for name in requested)
        alerts_reliable = normalized.status in {"active", "no_active_alerts"}
        payload["status"] = (
            "success"
            if (view == "alerts" and normalized.status != "failed")
            or (complete_weather and not stale_segments and alerts_reliable)
            else "partial_success"
        )
        payload["sources"] = [self._provider.name]
        payload["evidence_note"] = (
            "只引用本次返回的天气与预警字段。alert_check 不是 active/no_active_alerts 时，"
            "不得回答为没有官方预警；预报风险不等于已发布官方预警。"
        )
        return _json_result(
            payload,
            meta={
                "provider": self._provider.name,
                "duration_seconds": time.monotonic() - started,
                "requested_segments": sorted(tasks),
                "weather_segment_count": usable_weather,
                "alert_status": normalized.status,
                **({"segment_failures": _failure_diagnostics(failures)} if failures else {}),
            },
        )

    def _resolve_location(
        self,
        query: str,
        area: str | None,
        ref: str | None,
    ) -> LocationCandidate | ToolResult:
        if ref:
            selected = self._resolver.get_location(ref=ref)
            if selected is not None:
                return selected
            candidates = self._resolver.search_locations(
                query=query,
                administrative_area=area,
            )
            if not candidates:
                return _json_result(
                    {
                        "source_type": "weather_query",
                        "status": "no_location_results",
                        "reason": "之前选择的地点已失效，且当前没有新的可靠候选。",
                        "next_action": "请用户补充国家、城市、区县或更完整的地点。",
                    }
                )
            return _clarification(query, area, candidates, reason="之前选择的地点已失效。")
        candidates = self._resolver.search_locations(
            query=query,
            administrative_area=area,
        )
        selected = _unique_location(
            query,
            candidates,
            administrative_area=area,
        )
        if selected is not None:
            return selected
        if not candidates:
            return _json_result(
                {
                    "source_type": "weather_query",
                    "status": "no_location_results",
                    "location": query,
                    "next_action": "请用户补充国家、城市、区县或更完整的地点。",
                }
            )
        return _clarification(query, area, candidates)


def _validate_args(
    args: dict[str, Any],
) -> tuple[str, str | None, str | None, WeatherView, int, int, UnitSystem] | ToolResult:
    location = args.get("location")
    if not isinstance(location, str) or not location.strip():
        return _failure("invalid_arguments", "location 必须是用户明确提供的非空地点。")
    area_value = args.get("administrative_area")
    if area_value is not None and not isinstance(area_value, str):
        return _failure("invalid_arguments", "administrative_area 必须是字符串。")
    ref_value = args.get("location_ref")
    if ref_value is not None and (not isinstance(ref_value, str) or not ref_value.strip()):
        return _failure("invalid_arguments", "location_ref 必须原样使用有效候选引用。")
    view = args.get("view", "overview")
    if view not in {"overview", "current", "hourly", "daily", "alerts"}:
        return _failure("invalid_arguments", "view 不是支持的天气查询粒度。")
    hours = args.get("hours", 24)
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 72:
        return _failure("invalid_arguments", "hours 必须在 1 到 72 之间。")
    days = args.get("days", 3)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 10:
        return _failure("invalid_arguments", "days 必须在 1 到 10 之间。")
    units = args.get("unit_system", "metric")
    if units not in {"metric", "imperial"}:
        return _failure("invalid_arguments", "unit_system 只能是 metric 或 imperial。")
    area = area_value.strip() if isinstance(area_value, str) and area_value.strip() else None
    ref = ref_value.strip() if isinstance(ref_value, str) else None
    return location.strip(), area, ref, view, hours, days, units


def _requested_segments(view: WeatherView) -> tuple[str, ...]:
    if view == "overview":
        return ("current", "hourly", "daily")
    if view == "alerts":
        return ()
    return (view,)


def _unique_location(
    query: str,
    candidates: tuple[LocationCandidate, ...],
    *,
    administrative_area: str | None = None,
) -> LocationCandidate | None:
    if len(candidates) == 1:
        return candidates[0]
    normalized = _normalize(query)
    exact = [
        item
        for item in candidates
        if normalized in {_normalize(item.name), _normalize(item.address)}
    ]
    if len(exact) == 1:
        return exact[0]

    scoped_query = f"{administrative_area or ''}{query}"
    scoped_key = _administrative_key(scoped_query)
    if len(scoped_key) < 4:
        return None
    hierarchical = [
        item
        for item in candidates
        if scoped_key == _candidate_locality_key(item)
        or _administrative_key(item.address).endswith(scoped_key)
    ]
    return hierarchical[0] if len(hierarchical) == 1 else None


def _normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _administrative_key(value: str) -> str:
    key = _normalize(value)
    for marker in _ADMIN_DIVISION_MARKERS:
        key = key.replace(marker, "")
    return key


def _candidate_locality_key(item: LocationCandidate) -> str:
    locality = item.district or item.name
    components = (item.city, locality)
    return _administrative_key("".join(dict.fromkeys(part for part in components if part)))


def _clarification(
    query: str,
    area: str | None,
    candidates: tuple[LocationCandidate, ...],
    *,
    reason: str | None = None,
) -> ToolResult:
    payload: dict[str, Any] = {
        "source_type": "weather_query",
        "status": "needs_clarification",
        "location": query,
        "administrative_area": area,
        "candidates": [
            {
                "location_ref": item.ref,
                "name": item.name,
                "address": item.address,
                **({"country": item.country} if item.country else {}),
                **({"city": item.city} if item.city else {}),
                **({"district": item.district} if item.district else {}),
            }
            for item in candidates[:_MAX_CANDIDATES]
        ],
        "next_action": "请让用户选择当前展示的一个地点，再原样传回 location_ref。",
    }
    if reason:
        payload["reason"] = reason
    if len(candidates) > _MAX_CANDIDATES:
        payload["additional_candidate_count"] = len(candidates) - _MAX_CANDIDATES
        payload["candidate_note"] = "候选列表已截断，向用户澄清时必须说明仍有未展示候选。"
    return _json_result(payload)


def _normalize_alert_check(check: AlertCheck, now: datetime) -> AlertCheck:
    if check.status != "active":
        return check
    ordered = sorted(
        check.alerts,
        key=lambda item: (
            _aware_utc(item.issued_at) if item.issued_at else datetime.min.replace(tzinfo=UTC)
        ),
    )
    active: dict[str, WeatherAlert] = {}
    unverified: list[WeatherAlert] = []
    for alert in ordered:
        for superseded in alert.supersedes:
            active.pop(superseded, None)
        if alert.message_type.casefold() == "cancel":
            continue
        if alert.expires_at is None:
            unverified.append(alert)
            continue
        if _aware_utc(alert.expires_at) <= now:
            continue
        active[alert.alert_id] = alert
    if unverified:
        return AlertCheck(
            status="stale",
            checked_at=check.checked_at,
            alerts=tuple(_sort_alerts((*active.values(), *unverified))),
            message="部分预警缺少可核实的有效时间，不能确认其当前状态。",
        )
    values = tuple(_sort_alerts(tuple(active.values())))
    return AlertCheck(
        status="active" if values else "no_active_alerts",
        checked_at=check.checked_at,
        alerts=values,
        message=check.message,
    )


def _sort_alerts(alerts: tuple[WeatherAlert, ...]) -> list[WeatherAlert]:
    severity = {"extreme": 0, "severe": 1, "moderate": 2, "minor": 3, "unknown": 4}
    urgency = {"immediate": 0, "expected": 1, "future": 2, "past": 3, "unknown": 4}
    certainty = {"observed": 0, "likely": 1, "possible": 2, "unlikely": 3, "unknown": 4}
    return sorted(
        alerts,
        key=lambda item: (
            severity.get((item.severity or "unknown").casefold(), 5),
            urgency.get((item.urgency or "unknown").casefold(), 5),
            certainty.get((item.certainty or "unknown").casefold(), 5),
            -(_aware_utc(item.issued_at).timestamp() if item.issued_at else 0),
        ),
    )


def _alert_check_payload(check: AlertCheck) -> dict[str, Any]:
    alerts = check.alerts[:_MAX_ALERTS]
    payload: dict[str, Any] = {
        "status": check.status,
        "checked_at": check.checked_at.isoformat(),
        "alerts": [_jsonable(asdict(item)) for item in alerts],
    }
    if check.message:
        payload["message"] = check.message
    if len(check.alerts) > _MAX_ALERTS:
        payload["additional_alert_count"] = len(check.alerts) - _MAX_ALERTS
        payload["alert_note"] = "有效预警列表已截断，最终回答必须说明仍有未展示预警。"
    return payload


def _location_payload(location: LocationCandidate) -> dict[str, Any]:
    return {
        "name": location.name,
        "address": location.address,
        "longitude": location.longitude,
        "latitude": location.latitude,
        **({"city": location.city} if location.city else {}),
        **({"district": location.district} if location.district else {}),
        **({"country": location.country} if location.country else {}),
        **({"country_code": location.country_code} if location.country_code else {}),
        **({"timezone": location.timezone} if location.timezone else {}),
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise WeatherTemporaryError("天气时间缺少时区")
    return value.astimezone(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _provider_failure(
    exc: ToolError,
    *,
    provider: str,
    stage: str,
    failures: dict[str, ToolError] | None = None,
) -> ToolResult:
    meta: dict[str, Any] = {
        "provider": provider,
        "failure_stage": stage,
        **_failure_diagnostic(exc),
    }
    if failures:
        meta["segment_failures"] = _failure_diagnostics(failures)
    if isinstance(exc, WeatherAuthenticationError):
        return _failure(
            "configuration_required",
            "天气查询暂不可用：请在设置页配置天气服务凭据。",
            meta=meta,
        )
    if isinstance(exc, WeatherQuotaError):
        return _failure(
            "quota_exhausted",
            "天气查询额度已用完，当前无法可靠查询天气。",
            meta=meta,
        )
    if isinstance(exc, WeatherRateLimitError):
        return _failure(
            "rate_limited",
            "本轮天气查询因请求频率受限，未取得可用结果。",
            retry_mode="user_trigger_required",
            meta=meta,
        )
    if isinstance(exc, WeatherInvalidRequestError):
        return _failure(
            "invalid_request",
            "天气服务无法处理这次查询，请换更明确的地点或时间。",
            meta=meta,
        )
    if isinstance(exc, (WeatherTemporaryError, ToolError)):
        return _failure(
            "temporary_failure",
            "本轮天气查询未取得可用结果。",
            retry_mode="user_trigger_required",
            meta=meta,
        )
    return _failure(
        "temporary_failure",
        "本轮天气查询未取得可用结果。",
        retry_mode="user_trigger_required",
        meta=meta,
    )


def _failure(
    status: str,
    message: str,
    *,
    retry_mode: str | None = None,
    meta: dict[str, Any] | None = None,
) -> ToolResult:
    payload: dict[str, Any] = {
        "source_type": "weather_query",
        "status": status,
        "message": message,
    }
    if retry_mode is not None:
        payload["retry_mode"] = retry_mode
        payload["next_action"] = "如仍需要天气结果，由用户稍后重新发起查询。"
    return _json_result(
        payload,
        is_error=True,
        meta=meta,
    )


def _failure_diagnostic(exc: ToolError) -> dict[str, Any]:
    if isinstance(exc, WeatherError):
        return exc.diagnostic_meta()
    return {
        "error_kind": "tool",
        "exception_type": type(exc).__name__,
        "retryable": False,
    }


def _failure_diagnostics(failures: dict[str, ToolError]) -> dict[str, dict[str, Any]]:
    return {name: _failure_diagnostic(failures[name]) for name in sorted(failures)}


def _json_result(
    payload: dict[str, Any],
    *,
    is_error: bool = False,
    meta: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        text=json.dumps(payload, ensure_ascii=False, indent=2),
        is_error=is_error,
        meta=meta or {},
    )
