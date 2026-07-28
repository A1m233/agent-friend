"""共享地点解析器的最小契约。"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from .models import LocationCandidate


@runtime_checkable
class LocationResolver(Protocol):
    name: ClassVar[str]

    def search_locations(
        self,
        *,
        query: str,
        administrative_area: str | None,
    ) -> tuple[LocationCandidate, ...]: ...

    def get_location(self, *, ref: str) -> LocationCandidate | None: ...
