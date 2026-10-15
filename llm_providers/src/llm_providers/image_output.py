"""Bounded provider-neutral image output; never serialize image bodies as history."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Literal

MAX_OUTPUT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_IMAGES = 4


@dataclass(frozen=True)
class LLMImageStart:
    item_id: str
    output_index: int
    type: Literal["image_start"] = "image_start"


@dataclass(frozen=True)
class LLMImageResult:
    item_id: str
    output_index: int
    data: bytes = field(default=b"", repr=False)
    error_code: str | None = None
    type: Literal["image_result"] = "image_result"


def decode_image_result(
    item_id: str, output_index: int, result: object, status: object, count: int
) -> LLMImageResult:
    error = None
    data = b""
    if count > MAX_OUTPUT_IMAGES:
        error = "image_count_exceeded"
    elif status not in (None, "completed"):
        error = "image_generation_failed"
    elif not isinstance(result, str) or not result:
        error = "image_result_missing"
    elif len(result) > 4 * ((MAX_OUTPUT_IMAGE_BYTES + 2) // 3):
        error = "image_size_exceeded"
    else:
        try:
            data = base64.b64decode(result, validate=True)
        except (binascii.Error, ValueError):
            error = "image_invalid"
        if not error and (not data or len(data) > MAX_OUTPUT_IMAGE_BYTES):
            data, error = b"", "image_size_exceeded"
    return LLMImageResult(item_id, output_index, data, error)
