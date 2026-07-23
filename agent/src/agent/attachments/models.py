from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

AttachmentKind = Literal["image", "document"]


@dataclass(frozen=True)
class AttachmentRef:
    asset_id: str
    display_name: str
    kind: AttachmentKind
    media_type: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AttachmentRef:
        return cls(
            asset_id=str(value["asset_id"]),
            display_name=str(value["display_name"]),
            kind=value["kind"],
            media_type=str(value["media_type"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]),
        )
