"""Bounded static-image decoding; filesystem authority belongs to FilesystemService."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from ..attachments import AttachmentError, AttachmentRef, AttachmentStore
from ..attachments.models import TOOL_IMAGE_MAX_BYTES

MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_SIDE = 8192


@dataclass(frozen=True)
class ImageSnapshot:
    image: AttachmentRef
    width: int
    height: int


def _check_size(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or max(width, height) > MAX_IMAGE_SIDE
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise AttachmentError("image_dimensions_exceeded", "图片尺寸超限，请先缩小图片。")


def _check_webp_header(data: bytes) -> None:
    # libwebp may allocate its canvas during Image.open, before Pillow's pixel check.
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        if data[20] & 2:
            raise AttachmentError("image_animation_unsupported", "仅支持静态图片。")
        _check_size(
            1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        )
    elif chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        _check_size(width & 0x3FFF, height & 0x3FFF)
    elif chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        _check_size((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    else:
        raise AttachmentError("image_invalid", "WebP 图片头无效。")


def validate_image(data: bytes) -> tuple[int, int]:
    if not data or len(data) > TOOL_IMAGE_MAX_BYTES:
        raise AttachmentError("image_size_exceeded", "图片为空或超过 8 MiB，请提供更小的图片。")
    _check_webp_header(data)
    try:
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
            width, height = image.size
            _check_size(width, height)
            if getattr(image, "n_frames", 1) != 1:
                raise AttachmentError("image_animation_unsupported", "仅支持静态 PNG/JPEG/WebP。")
            image.verify()
        with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
            image.load()
    except AttachmentError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise AttachmentError(
            "image_invalid", "图片损坏、格式不支持或解码超限，请换有效静态图片。"
        ) from exc
    return width, height


class ImageReadingService:
    def __init__(self, store: AttachmentStore) -> None:
        self._store = store

    def snapshot(self, data: bytes, *, display_name: str) -> ImageSnapshot:
        width, height = validate_image(data)
        # Keep the lease until it expires: publication can fail or race with GC.
        # Once published, event reachability owns retention independently of the lease.
        _lease, ref = self._store.import_image_bytes(data, display_name=display_name)
        return ImageSnapshot(ref, width, height)
