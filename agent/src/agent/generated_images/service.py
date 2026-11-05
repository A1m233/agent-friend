from __future__ import annotations

import hashlib
import json

from llm_providers.image_output import LLMImageResult

from ..attachments import AttachmentError, AttachmentRef, AttachmentStore
from ..image_reading.service import ImageReadingService

_FAILURE_REASONS = {
    "image_interrupted": "图片生成已中断，未取得完整图片",
    "image_storage_failed": "图片未能保存",
    "image_store_unavailable": "图片保存服务不可用",
}


def save_generated_image(
    event: LLMImageResult, store: AttachmentStore | None, message_id: str
) -> tuple[str, tuple[AttachmentRef, ...], str | None]:
    error = event.error_code
    image = None
    if not error:
        if store is None:
            error = "image_store_unavailable"
        else:
            try:
                image = (
                    ImageReadingService(store)
                    .snapshot(event.data, display_name=f"generated-{message_id}")
                    .image
                )
            except AttachmentError as exc:
                error = exc.code
            except OSError:
                error = "image_storage_failed"
    if image is None:
        error = error or "image_invalid"
        reason = _FAILURE_REASONS.get(error, "未能取得可用图片")
        return f"{reason}；不要宣称图片已交付。", (), error
    file_ref = "file-" + hashlib.sha256(f"{message_id}:0".encode()).hexdigest()
    text = (
        "[助手生成图片已保存："
        + json.dumps(
            {
                "source": "assistant_generated",
                "file_ref": file_ref,
                "source_message_ref": message_id,
                "name": image.display_name,
            },
            ensure_ascii=False,
        )
        + "] 可用 session_files prepare 或 view_image 按需读取；保存不等于渠道已送达。"
    )
    return text, (image,), None
