"""Final Chat request preparation; native Responses bypasses this boundary."""

from __future__ import annotations

import copy
from typing import Any

from .deepseek_vision_compat import flatten_deepseek_file_blocks, is_deepseek_vision_spec
from .errors import LLMBadRequestError
from .spec import ProviderSpec


def prepare_litellm_request(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply known model compatibility without changing caller-owned inputs."""

    from .multimodal.chat_images import project_chat_tool_images

    # Phase belongs to Responses history, not the Chat wire contract.
    messages = project_chat_tool_images(chat_replay_messages(messages))
    if spec.provider_id is not None and spec.provider_id.startswith("custom/"):
        # The connection declares OpenAI wire compatibility, not a LiteLLM model
        # family. Keep arbitrary effort strings out of its model-specific mapper.
        prepared = copy.deepcopy(options)
        if "reasoning_effort" in prepared:
            extra = prepared.setdefault("extra_body", {})
            if not isinstance(extra, dict) or "reasoning_effort" in extra:
                raise LLMBadRequestError("conflicting reasoning settings")
            extra["reasoning_effort"] = prepared.pop("reasoning_effort")
        return messages, prepared
    deepseek_vision = is_deepseek_vision_spec(spec)
    if not deepseek_vision and spec.model != "openai/kimi-k3":
        return messages, options

    prepared_options = dict(options)
    if deepseek_vision:
        wire_messages = flatten_deepseek_file_blocks(messages)
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
        # These models support this parameter, but LiteLLM's generic OpenAI
        # model metadata does not. Preserve the selected effort at the wire.
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


def chat_replay_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only explicit same-response items for Chat's tool transaction."""
    if not any("phase" in message or "_assistant_response_id" in message for message in messages):
        return messages
    result: list[dict[str, Any]] = []
    previous_group: object = None
    for message in messages:
        group = message.get("_assistant_response_id")
        wire = {
            key: value
            for key, value in message.items()
            if key not in {"phase", "_assistant_response_id"}
        }
        if group and group == previous_group and message.get("role") == "assistant":
            previous = result[-1]
            previous["content"] = (previous.get("content") or "") + (wire.get("content") or "")
            if wire.get("tool_calls"):
                previous["tool_calls"] = [*previous.get("tool_calls", []), *wire["tool_calls"]]
        else:
            result.append(wire)
        previous_group = group if message.get("role") == "assistant" else None
    return result
