from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from llm_providers.multimodal.limits import TOOL_RESULT_MAX_IMAGE_BYTES, TOOL_RESULT_MAX_IMAGES

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


TOOL_IMAGE_MAX_BYTES = 8 * 1024 * 1024
TOOL_IMAGE_MAX_COUNT = TOOL_RESULT_MAX_IMAGES
TOOL_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def validate_image_refs(images: Sequence[AttachmentRef]) -> None:
    if len(images) > TOOL_IMAGE_MAX_COUNT:
        raise ValueError("too many tool images")
    if sum(ref.size_bytes for ref in images) > TOOL_RESULT_MAX_IMAGE_BYTES:
        raise ValueError("tool images exceed total byte budget")
    for ref in images:
        if (
            ref.kind != "image"
            or ref.media_type not in TOOL_IMAGE_MEDIA_TYPES
            or not 0 < ref.size_bytes <= TOOL_IMAGE_MAX_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", ref.asset_id)
            or ref.asset_id != ref.sha256
        ):
            raise ValueError("invalid tool image reference")


def parse_image_refs(value: Any) -> tuple[AttachmentRef, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("images must be an array")
    refs = tuple(AttachmentRef.from_dict(item) for item in value)
    validate_image_refs(refs)
    return refs
