"""Narrow request-shape compatibility for DeepSeek's vision model.

LiteLLM's generic OpenAI-compatible route validates nested ``file.file_id``
content blocks, while DeepSeek's chat endpoint expects a flat ``file_id``.
This module owns only that final wire conversion and is gated to one exact
LiteLLM model id.
"""

from __future__ import annotations

import copy
from typing import Any

from .catalog import DEEPSEEK_VISION_LITELLM_MODEL
from .errors import LLMBadRequestError
from .spec import ProviderSpec


def is_deepseek_vision_spec(spec: ProviderSpec) -> bool:
    return spec.provider_id in {None, "deepseek"} and spec.model == DEEPSEEK_VISION_LITELLM_MODEL


def flatten_deepseek_file_blocks(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a deep-copied DeepSeek wire representation of ``messages``."""

    wire_messages = copy.deepcopy(messages)
    for message in wire_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "file":
                continue
            if message.get("role") != "user":
                raise LLMBadRequestError(
                    "attachment_wire_invalid: DeepSeek 文件引用只能出现在 user message。"
                )
            if set(block) != {"type", "file"}:
                raise LLMBadRequestError("attachment_wire_invalid: DeepSeek 文件引用包含未知字段。")
            nested = block.get("file")
            if not isinstance(nested, dict) or set(nested) != {"file_id"}:
                raise LLMBadRequestError("attachment_wire_invalid: DeepSeek 文件引用结构无效。")
            file_id = nested.get("file_id")
            if not isinstance(file_id, str) or not file_id.strip():
                raise LLMBadRequestError("attachment_wire_invalid: DeepSeek 文件引用缺少 file_id。")
            content[index] = {"type": "file", "file_id": file_id}
    return wire_messages
