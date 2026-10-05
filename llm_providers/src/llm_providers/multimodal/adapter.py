"""Render tool-image snapshots; user files must first be projected to references."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from typing import Any, BinaryIO, cast

from ..catalog import DEEPSEEK_VISION_LITELLM_MODEL, model_capabilities
from ..client import LLMClient
from ..errors import LLMBadRequestError
from ..protocol import LanguageModel
from .tool_images import supports_tool_images


class MultimodalInputAdapter:
    def __init__(self, *, open_asset: Callable[[str], BinaryIO]) -> None:
        self._open_asset = open_asset

    def render_message(self, message: Any, client: LanguageModel) -> dict[str, Any]:
        if getattr(message, "images", ()):
            return self._render_tool_images(message, client)
        if getattr(message, "attachments", ()):
            raise LLMBadRequestError(
                "attachment_projection_required: 用户附件必须通过会话文件投影按需读取。"
            )
        return cast(dict[str, Any], message.to_openai())

    @staticmethod
    def _data_url(media_type: str, data: bytes) -> str:
        return f"data:{media_type};base64," + base64.b64encode(data).decode("ascii")

    def _render_tool_images(self, message: Any, client: LanguageModel) -> dict[str, Any]:
        if message.role != "tool":
            raise LLMBadRequestError("image_source_invalid: images require tool origin")
        result = {"role": "tool", "tool_call_id": message.meta.get("tool_call_id", "")}
        text = str(message.content or "")
        if not supports_tool_images(client):
            return {
                **result,
                "content": text
                + "\n[工具图像当前未提供：当前模型路由不支持图像工具结果，不能推断原图内容]",
            }
        concrete = self._concrete_client(client)
        caps = concrete.spec.attachment_capabilities or model_capabilities(
            self._canonical_model_id(concrete)
        )
        images = message.images
        if len(images) > min(4, caps.max_attachments_per_turn):
            raise LLMBadRequestError("image_count_exceeded: too many tool images")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        limit = min(8 * 1024 * 1024, caps.max_attachment_bytes)
        if sum(item.size_bytes for item in images) > caps.max_total_attachment_bytes:
            raise LLMBadRequestError("image_total_size_exceeded")
        for ref in images:
            if ref.kind != "image" or ref.media_type not in caps.accepted_media_types:
                content.append({"type": "text", "text": "[工具图像未提供：当前模型不支持此格式]"})
                continue
            if not 0 < ref.size_bytes <= limit:
                content.append({"type": "text", "text": "[工具图像未提供：大小超过当前模型限制]"})
                continue
            try:
                with self._open_asset(ref.asset_id) as source:
                    data = source.read(limit + 1)
                if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
                    raise ValueError("image_integrity_failed")
            except (OSError, ValueError):
                content.append(
                    {
                        "type": "text",
                        "text": "[工具图像未提供：原资产缺失或损坏，请重新读取有效图片]",
                    }
                )
                continue
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._data_url(ref.media_type, data),
                        "detail": "auto",
                    },
                }
            )
        return {**result, "content": content}

    @staticmethod
    def _canonical_model_id(client: LLMClient) -> str:
        model = client.spec.model
        if model == DEEPSEEK_VISION_LITELLM_MODEL:
            return "deepseek/deepseek-flash"
        if model.startswith("openai/kimi-"):
            return "kimi/" + model.removeprefix("openai/")
        return model

    @staticmethod
    def _concrete_client(client: LanguageModel) -> LLMClient:
        if not isinstance(client, LLMClient):
            raise LLMBadRequestError("attachment_model_unsupported: 当前模型路由没有附件适配器。")
        return client
