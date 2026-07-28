"""高德 Web 服务 V5 的附近地点 provider。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..models import (
    AnchorCandidate,
    NearbyPlace,
    NearbyPlaceAuthenticationError,
    NearbyPlaceInvalidRequestError,
    NearbyPlaceQuotaError,
    NearbyPlaceRateLimitError,
    NearbyPlaceTemporaryError,
    PlaceSort,
)

_BASE_URL = "https://restapi.amap.com"

_AUTH_INFOCODES = {
    "10001",  # INVALID_USER_KEY
    "10002",  # SERVICE_NOT_AVAILABLE
    "10005",  # INVALID_USER_IP
    "10006",  # INVALID_USER_DOMAIN
    "10007",  # INVALID_USER_SIGNATURE
    "10008",  # INVALID_USER_SCODE
    "10009",  # USERKEY_PLAT_NOMATCH
    "10011",  # NOT_SUPPORT_HTTPS
    "10012",  # INSUFFICIENT_PRIVILEGES
    "10013",  # USER_KEY_RECYCLED
}
_QUOTA_INFOCODES = {"10003", "10010", "10044"}
_RATE_LIMIT_INFOCODES = {
    "10004",
    "10014",
    "10019",
    "10020",
    "10021",
    "10022",
    "10023",
    "10024",
}
_TEMPORARY_INFOCODES = {"10015", "10016", "10017"}


class AmapNearbyPlaceProvider:
    """把高德原生响应归一化为附近地点领域类型。"""

    name: ClassVar[str] = "amap"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key.strip():
            raise NearbyPlaceAuthenticationError("AMAP_WEBSERVICE_KEY 为空")
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    def search_anchors(self, *, near: str, city: str | None) -> tuple[AnchorCandidate, ...]:
        params: dict[str, Any] = {
            "keywords": near,
            "page_size": 10,
            "page_num": 1,
            "output": "json",
        }
        if city:
            params.update({"region": city, "city_limit": "true"})
        payload = self._get("/v5/place/text", params=params)
        return tuple(
            candidate
            for raw in _items(payload, "pois")
            if (candidate := _parse_anchor(raw)) is not None
        )

    def search_locations(
        self,
        *,
        query: str,
        administrative_area: str | None,
    ) -> tuple[AnchorCandidate, ...]:
        """满足共享 LocationResolver；附近地点旧接口继续保留。"""

        return tuple(
            AnchorCandidate(
                ref=f"amap:{item.ref}",
                name=item.name,
                address=item.address,
                city=item.city,
                district=item.district,
                longitude=item.longitude,
                latitude=item.latitude,
                country=item.country,
                country_code=item.country_code,
                timezone=item.timezone,
            )
            for item in self.search_anchors(near=query, city=administrative_area)
        )

    def get_anchor(self, *, ref: str) -> AnchorCandidate | None:
        payload = self._get(
            "/v5/place/detail",
            params={"id": ref, "output": "json"},
        )
        for raw in _items(payload, "pois"):
            candidate = _parse_anchor(raw)
            if candidate is not None:
                return candidate
        return None

    def get_location(self, *, ref: str) -> AnchorCandidate | None:
        if not ref.startswith("amap:"):
            return None
        item = self.get_anchor(ref=ref.removeprefix("amap:"))
        if item is None:
            return None
        return AnchorCandidate(
            ref=ref,
            name=item.name,
            address=item.address,
            city=item.city,
            district=item.district,
            longitude=item.longitude,
            latitude=item.latitude,
            country=item.country,
            country_code=item.country_code,
            timezone=item.timezone,
        )

    def search_nearby(
        self,
        *,
        query: str,
        anchor: AnchorCandidate,
        radius_meters: int,
        sort: PlaceSort,
        limit: int,
    ) -> tuple[NearbyPlace, ...]:
        payload = self._get(
            "/v5/place/around",
            params={
                "keywords": query,
                "location": f"{anchor.longitude:.6f},{anchor.latitude:.6f}",
                "radius": radius_meters,
                "sortrule": "distance" if sort == "distance" else "weight",
                "show_fields": "business",
                "page_size": 25,
                "page_num": 1,
                "output": "json",
            },
        )
        indexed_places = [
            (index, place)
            for index, raw in enumerate(_items(payload, "pois"))
            if (place := _parse_nearby_place(raw)) is not None
        ]
        if sort == "distance":
            indexed_places.sort(key=lambda item: (item[1].distance_meters, item[0]))
        return tuple(place for _, place in indexed_places[:limit])

    def _get(self, path: str, *, params: Mapping[str, Any]) -> dict[str, Any]:
        request_params = {**params, "key": self._api_key}
        try:
            if self._client is not None:
                response = self._client.get(path, params=request_params)
            else:
                with httpx.Client(
                    base_url=_BASE_URL,
                    timeout=self._timeout_seconds,
                ) as client:
                    response = client.get(path, params=request_params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise NearbyPlaceTemporaryError("地点服务网络请求失败") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise NearbyPlaceAuthenticationError("地点服务拒绝凭据") from exc
            if exc.response.status_code == 429:
                raise NearbyPlaceRateLimitError("地点服务请求过于频繁") from exc
            raise NearbyPlaceTemporaryError("地点服务 HTTP 错误") from exc
        except ValueError as exc:
            raise NearbyPlaceTemporaryError("地点服务返回无法解析的数据") from exc

        if not isinstance(payload, dict):
            raise NearbyPlaceTemporaryError("地点服务返回结构异常")
        _raise_for_amap_error(payload)
        return payload


def _raise_for_amap_error(payload: Mapping[str, Any]) -> None:
    if str(payload.get("status", "")) == "1":
        return
    infocode = _text(payload.get("infocode"))
    if infocode in _AUTH_INFOCODES:
        raise NearbyPlaceAuthenticationError("地点服务凭据无效或无权限")
    if infocode in _QUOTA_INFOCODES:
        raise NearbyPlaceQuotaError("地点服务额度已耗尽")
    if infocode in _RATE_LIMIT_INFOCODES:
        raise NearbyPlaceRateLimitError("地点服务请求过于频繁")
    if infocode in _TEMPORARY_INFOCODES:
        raise NearbyPlaceTemporaryError("地点服务暂时不可用")
    if infocode.startswith("2"):
        raise NearbyPlaceInvalidRequestError("地点服务拒绝了查询参数")
    raise NearbyPlaceTemporaryError("地点服务返回未分类错误")


def _items(payload: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    raw_items = payload.get(key)
    if not isinstance(raw_items, list):
        return ()
    return tuple(item for item in raw_items if isinstance(item, Mapping))


def _parse_anchor(raw: Mapping[str, Any]) -> AnchorCandidate | None:
    ref = _text(raw.get("id"))
    name = _text(raw.get("name"))
    address = _full_address(raw)
    location = _parse_location(raw.get("location"))
    if not ref or not name or not address or location is None:
        return None
    longitude, latitude = location
    return AnchorCandidate(
        ref=ref,
        name=name,
        address=address,
        city=_optional_text(raw.get("cityname")),
        district=_optional_text(raw.get("adname")),
        longitude=longitude,
        latitude=latitude,
    )


def _parse_nearby_place(raw: Mapping[str, Any]) -> NearbyPlace | None:
    name = _text(raw.get("name"))
    address = _full_address(raw)
    distance = _parse_distance(raw.get("distance"))
    if not name or not address or distance is None:
        return None
    business = raw.get("business")
    business_fields = business if isinstance(business, Mapping) else {}
    opening_hours = _optional_text(business_fields.get("opentime_week"))
    if opening_hours is None:
        opening_hours = _optional_text(business_fields.get("opentime_today"))
    return NearbyPlace(
        name=name,
        distance_meters=distance,
        address=address,
        telephone=_optional_text(business_fields.get("tel")),
        opening_hours=opening_hours,
    )


def _full_address(raw: Mapping[str, Any]) -> str:
    address = _text(raw.get("address"))
    if not address:
        return ""
    prefix_parts = [
        _text(raw.get("pname")),
        _text(raw.get("cityname")),
        _text(raw.get("adname")),
    ]
    prefix = "".join(
        part for index, part in enumerate(prefix_parts) if part and part not in prefix_parts[:index]
    )
    if prefix and not address.startswith(prefix):
        return f"{prefix}{address}"
    return address


def _parse_location(value: Any) -> tuple[float, float] | None:
    text = _text(value)
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        longitude, latitude = (float(part) for part in parts)
    except ValueError:
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def _parse_distance(value: Any) -> int | None:
    text = _text(value)
    try:
        distance = round(float(text))
    except (TypeError, ValueError):
        return None
    return distance if distance >= 0 else None


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
