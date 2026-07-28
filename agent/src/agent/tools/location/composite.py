"""主地点解析器与可选 fallback 的保守组合。"""

from __future__ import annotations

from typing import ClassVar

from .models import LocationCandidate
from .protocol import LocationResolver


class CompositeLocationResolver:
    """主解析器无候选时才调用 fallback，避免跨源候选混排。"""

    name: ClassVar[str] = "composite"

    def __init__(
        self,
        primary: LocationResolver,
        fallback: LocationResolver | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def search_locations(
        self,
        *,
        query: str,
        administrative_area: str | None,
    ) -> tuple[LocationCandidate, ...]:
        candidates = self._primary.search_locations(
            query=query,
            administrative_area=administrative_area,
        )
        if candidates or self._fallback is None:
            return candidates
        return self._fallback.search_locations(
            query=query,
            administrative_area=administrative_area,
        )

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        candidate = self._primary.get_location(ref=ref)
        if candidate is not None or self._fallback is None:
            return candidate
        return self._fallback.get_location(ref=ref)
