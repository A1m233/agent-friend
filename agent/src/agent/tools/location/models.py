"""工具间共享的可靠地点候选模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocationCandidate:
    """由受信 provider 解析出的可复用地点候选。"""

    ref: str
    name: str
    address: str
    city: str | None
    district: str | None
    longitude: float
    latitude: float
    country: str | None = None
    country_code: str | None = None
    timezone: str | None = None

    @property
    def display_address(self) -> str:
        return self.address

    @property
    def administrative_area(self) -> str | None:
        return self.district or self.city
