"""附近地点搜索的 provider-agnostic 领域类型与错误模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, NoReturn, Protocol, runtime_checkable

from ...errors import ToolError

PlaceSort = Literal["distance", "relevance"]


@dataclass(frozen=True)
class AnchorCandidate:
    """可作为附近搜索圆心的地点候选。"""

    ref: str
    name: str
    address: str
    city: str | None
    district: str | None
    longitude: float
    latitude: float


@dataclass(frozen=True)
class NearbyPlace:
    """附近搜索结果的最小可信字段。"""

    name: str
    distance_meters: int
    address: str
    telephone: str | None = None
    opening_hours: str | None = None


class NearbyPlaceError(ToolError):
    """附近地点 provider 错误基类。"""


class NearbyPlaceAuthenticationError(NearbyPlaceError):
    """凭据缺失、无效或无服务权限。"""


class NearbyPlaceQuotaError(NearbyPlaceError):
    """调用额度已耗尽。"""


class NearbyPlaceRateLimitError(NearbyPlaceError):
    """请求频率超过 provider 限制。"""


class NearbyPlaceInvalidRequestError(NearbyPlaceError):
    """provider 判定请求参数无效。"""


class NearbyPlaceTemporaryError(NearbyPlaceError):
    """网络或 provider 临时故障。"""


@runtime_checkable
class NearbyPlaceProvider(Protocol):
    """仅供 nearby_place_search 子包消费的 provider 契约。"""

    name: ClassVar[str]

    def search_anchors(self, *, near: str, city: str | None) -> tuple[AnchorCandidate, ...]: ...

    def get_anchor(self, *, ref: str) -> AnchorCandidate | None: ...

    def search_nearby(
        self,
        *,
        query: str,
        anchor: AnchorCandidate,
        radius_meters: int,
        sort: PlaceSort,
        limit: int,
    ) -> tuple[NearbyPlace, ...]: ...


class UnavailableNearbyPlaceProvider:
    """未配置地点凭据时保留工具表面的 provider。"""

    name: ClassVar[str] = "unconfigured"

    def _raise(self) -> NoReturn:
        raise NearbyPlaceAuthenticationError("地点服务凭据未配置")

    def search_anchors(self, *, near: str, city: str | None) -> tuple[AnchorCandidate, ...]:
        self._raise()

    def get_anchor(self, *, ref: str) -> AnchorCandidate | None:
        self._raise()

    def search_nearby(
        self,
        *,
        query: str,
        anchor: AnchorCandidate,
        radius_meters: int,
        sort: PlaceSort,
        limit: int,
    ) -> tuple[NearbyPlace, ...]:
        self._raise()
