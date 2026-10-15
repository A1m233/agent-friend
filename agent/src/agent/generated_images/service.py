from __future__ import annotations

import hashlib
import json

from llm_providers.image_output import LLMImageResult

from ..attachments import AttachmentError, AttachmentRef, AttachmentStore
from ..image_reading.service import ImageReadingService

IMAGE_FAILURE_TEXT = "图片未能保存，请在当前回复中查看失败状态；不要宣称图片已交付。"


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
        return IMAGE_FAILURE_TEXT, (), error or "image_invalid"
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
