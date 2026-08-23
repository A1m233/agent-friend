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
    return spec.model == DEEPSEEK_VISION_LITELLM_MODEL


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


def prepare_litellm_request(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add DeepSeek wire compatibility without mutating caller-owned inputs."""

    if not is_deepseek_vision_spec(spec):
        return messages, options

    wire_messages = flatten_deepseek_file_blocks(messages)
    prepared_options = dict(options)
    raw_extra_body = prepared_options.get("extra_body")
    if raw_extra_body is None:
        extra_body: dict[str, Any] = {}
    elif isinstance(raw_extra_body, dict):
        extra_body = copy.deepcopy(raw_extra_body)
    else:
        raise LLMBadRequestError(
            "attachment_wire_invalid: DeepSeek request extra_body 必须是对象。"
        )
    extra_body["messages"] = wire_messages
    prepared_options["extra_body"] = extra_body
    if "reasoning_effort" in prepared_options:
        raw_allowed = prepared_options.get("allowed_openai_params")
        if raw_allowed is None:
            allowed: list[str] = []
        elif isinstance(raw_allowed, (list, tuple)) and all(
            isinstance(item, str) for item in raw_allowed
        ):
            allowed = list(raw_allowed)
        else:
            raise LLMBadRequestError(
                "provider_request_invalid: allowed_openai_params 必须是字符串序列。"
            )
        if "reasoning_effort" not in allowed:
            allowed.append("reasoning_effort")
        prepared_options["allowed_openai_params"] = allowed
    return messages, prepared_options
