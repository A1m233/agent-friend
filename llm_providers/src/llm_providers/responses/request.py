"""Convert project Chat-shaped requests into native Responses requests."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

from ..errors import LLMBadRequestError
from ..spec import ProviderSpec


def _invalid(message: str) -> LLMBadRequestError:
    return LLMBadRequestError(f"responses_request_invalid: {message}")


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{name} must be a nonempty string")
    return value


def _content(value: Any, *, assistant: bool = False) -> list[dict[str, Any]]:
    text_type = "output_text" if assistant else "input_text"
    if value is None:
        return []
    if isinstance(value, str):
        return [{"type": text_type, "text": value}] if value else []
    if not isinstance(value, list):
        raise _invalid("message content must be text or content blocks")
    result: list[dict[str, Any]] = []
    for block in value:
        if not isinstance(block, dict):
            raise _invalid("content block must be an object")
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text"}:
            if set(block) != {"type", "text"} or not isinstance(block.get("text"), str):
                raise _invalid("invalid text block")
            result.append({"type": text_type, "text": block["text"]})
        elif kind == "image_url" and not assistant:
            image = block.get("image_url")
            if (
                set(block) != {"type", "image_url"}
                or not isinstance(image, dict)
                or set(image) - {"url", "detail"}
            ):
                raise _invalid("invalid image_url block")
            output = {"type": "input_image", "image_url": _string(image.get("url"), "image url")}
            if "detail" in image:
                output["detail"] = image["detail"]
            result.append(output)
        elif kind == "file" and not assistant:
            file = block.get("file")
            if set(block) != {"type", "file"} or not isinstance(file, dict):
                raise _invalid("invalid file block")
            if set(file) == {"file_id"}:
                result.append(
                    {"type": "input_image", "file_id": _string(file.get("file_id"), "file_id")}
                )
            elif set(file) == {"filename", "file_data"}:
                result.append(
                    {
                        "type": "input_file",
                        "filename": _string(file.get("filename"), "filename"),
                        "file_data": _string(file.get("file_data"), "file_data"),
                    }
                )
            else:
                raise _invalid("unsupported file block")
        else:
            raise _invalid("unsupported content block")
    return result


def convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    # Chat providers may reuse an ID after its result, whereas Responses needs
    # request-wide unique call IDs. Rewrite only the wire projection, in order,
    # so appending history cannot change an already converted prefix.
    used_call_ids: set[str] = set()
    pending_calls: dict[str, str] = {}
    completed_calls: set[str] = set()
    next_suffix: dict[str, int] = {}
    allowed = {
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
        "reasoning_content",
        "reasoning_details",
        "phase",
        "_assistant_response_id",
    }
    for message in messages:
        if not isinstance(message, dict) or set(message) - allowed:
            raise _invalid("unsupported message fields")
        role = message.get("role")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise _invalid("unsupported message role")
        if role != "assistant" and any(
            message.get(key) is not None
            for key in ("tool_calls", "reasoning_content", "reasoning_details", "phase")
        ):
            raise _invalid("assistant-only fields on another role")
        if role != "tool" and message.get("tool_call_id") is not None:
            raise _invalid("tool_call_id requires a tool result")
        if role == "tool":
            source_id = _string(message.get("tool_call_id"), "tool_call_id")
            if source_id not in pending_calls and (
                source_id in completed_calls or source_id in used_call_ids
            ):
                raise _invalid("tool result has no pending call for an already used id")
            call_id = pending_calls.pop(source_id, source_id)
            completed_calls.add(source_id)
            used_call_ids.add(call_id)
            output = message.get("content")
            if not isinstance(output, str):
                output = _content(output)
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            )
            continue
        reasoning = message.get("reasoning_content")
        details = message.get("reasoning_details")
        if reasoning and details:
            raise _invalid("ambiguous reasoning state")
        if reasoning:
            items.append(
                {
                    "type": "reasoning",
                    "content": [
                        {"type": "reasoning_text", "text": _string(reasoning, "reasoning_content")}
                    ],
                }
            )
        if details:
            if not isinstance(details, (list, tuple)) or any(
                not isinstance(item, dict) or item.get("type") != "reasoning" for item in details
            ):
                raise _invalid("reasoning_details must contain native reasoning items")
            items.extend(copy.deepcopy(details))
        content = _content(message.get("content"), assistant=role == "assistant")
        if content or role != "assistant":
            item: dict[str, Any] = {"type": "message", "role": role, "content": content}
            phase = message.get("phase")
            if phase is not None:
                if phase not in {"commentary", "final_answer"}:
                    raise _invalid("unsupported assistant phase")
                item["phase"] = phase
            items.append(item)
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise _invalid("tool_calls must be an array")
        for call in calls:
            if (
                not isinstance(call, dict)
                or set(call) - {"id", "type", "function"}
                or call.get("type") != "function"
                or not isinstance(call.get("function"), dict)
            ):
                raise _invalid("only function tool calls are supported")
            function = call["function"]
            if set(function) - {"name", "arguments"}:
                raise _invalid("unsupported function call fields")
            source_id = _string(call.get("id"), "call id")
            if source_id in pending_calls:
                raise _invalid("ambiguous repeated tool call id before its result")
            call_id = source_id
            suffix = next_suffix.get(source_id, 1)
            while call_id in used_call_ids:
                digest = hashlib.sha256(f"{source_id}:{suffix}".encode()).hexdigest()
                call_id = f"call_{digest[:32]}"
                suffix += 1
            next_suffix[source_id] = suffix
            used_call_ids.add(call_id)
            pending_calls[source_id] = call_id
            items.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": _string(function.get("name"), "function name"),
                    "arguments": _string(function.get("arguments"), "arguments"),
                }
            )
    return items


def convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or set(tool) != {"type", "function"}
            or tool["type"] != "function"
        ):
            raise _invalid("only function tools are supported")
        function = tool["function"]
        if not isinstance(function, dict) or set(function) - {
            "name",
            "description",
            "parameters",
            "strict",
        }:
            raise _invalid("unsupported function definition")
        _string(function.get("name"), "function name")
        if not isinstance(function.get("parameters"), dict):
            raise _invalid("function parameters must be a JSON schema object")
        # Preserve schema/strict exactly. Opting into strict by default changes
        # optional-field semantics and is not a transparent transport conversion.
        result.append({"type": "function", **copy.deepcopy(function)})
    return result


def prepare_request(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    route = spec.responses
    if route is None:
        raise _invalid("native route is not configured")
    options = copy.deepcopy({**spec.defaults, **overrides})
    extra = options.pop("extra_body", {})
    if not isinstance(extra, dict):
        raise _invalid("extra_body must be an object")
    if set(extra) - {"thinking", "reasoning"}:
        raise _invalid("unsupported extra_body fields")
    result: dict[str, Any] = {
        "model": route.model,
        "input": convert_messages(messages),
        "stream": True,
    }
    if tools:
        result["tools"] = convert_tools(tools)
        if route.parameter_profile == "openai":
            # Chat's omitted strict flag is non-strict; Responses defaults differ.
            for tool in result["tools"]:
                tool.setdefault("strict", False)
    budget_keys = [
        key
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
        if key in options
    ]
    if len(budget_keys) > 1:
        raise _invalid("conflicting output token limits")
    if budget_keys:
        result["max_output_tokens"] = options.pop(budget_keys[0])
    if "reasoning" in options and "reasoning" in extra:
        raise _invalid("conflicting reasoning settings")
    reasoning = options.pop("reasoning", extra.pop("reasoning", None))
    effort = options.pop("reasoning_effort", None)
    if effort is not None:
        if reasoning is not None:
            raise _invalid("conflicting reasoning settings")
        reasoning = {"effort": effort}
    if reasoning is not None and not isinstance(reasoning, dict):
        raise _invalid("reasoning must be an object")
    thinking = extra.pop("thinking", None)
    if thinking is not None:
        if route.parameter_profile != "deepseek":
            raise _invalid("thinking is not supported by this Responses route")
        if (
            not isinstance(thinking, dict)
            or set(thinking) != {"type"}
            or not isinstance(thinking["type"], str)
            or thinking["type"] not in {"disabled", "enabled"}
        ):
            raise _invalid("invalid thinking toggle")
        # DeepSeek Responses ignores Chat's top-level thinking parameter.
        # Its native reasoning.effort controls BOTH the toggle and effort.
        native_effort = (reasoning or {}).get("effort")
        if thinking["type"] == "disabled":
            if native_effort not in {None, "none"}:
                raise _invalid("thinking disabled conflicts with reasoning effort")
            reasoning = {**(reasoning or {}), "effort": "none"}
        elif native_effort == "none":
            raise _invalid("thinking enabled conflicts with reasoning effort")
    if reasoning is not None:
        if route.parameter_profile in {"deepseek", "kimi"}:
            accepted_efforts = (
                {"low", "high", "max"}
                if route.parameter_profile == "kimi"
                else {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
            )
            if set(reasoning) - {"effort"} or (
                "effort" in reasoning
                and (
                    not isinstance(reasoning["effort"], str)
                    or reasoning["effort"] not in accepted_efforts
                )
            ):
                raise _invalid("unsupported reasoning configuration")
        result["reasoning"] = reasoning
    choice = options.pop("tool_choice", None)
    if choice is not None:
        if isinstance(choice, dict):
            function = choice.get("function", {})
            if choice.get("type") != "function" or not isinstance(function, dict):
                raise _invalid("invalid tool_choice")
            choice = {"type": "function", "name": _string(function.get("name"), "tool choice name")}
        elif not isinstance(choice, str) or choice not in {"auto", "none", "required"}:
            raise _invalid("unsupported tool_choice")
        if route.parameter_profile == "kimi" and choice != "auto":
            raise _invalid("Kimi Responses currently supports only tool_choice=auto")
        result["tool_choice"] = choice
    output_format = options.pop("response_format", None)
    if output_format is not None:
        if not isinstance(output_format, dict):
            raise _invalid("response_format must be an object")
        if output_format.get("type") == "json_schema":
            schema = output_format.get("json_schema")
            if not isinstance(schema, dict):
                raise _invalid("json_schema must be an object")
            output_format = {"type": "json_schema", **schema}
        elif output_format.get("type") not in {"text", "json_object"}:
            raise _invalid("unsupported response_format")
        if route.parameter_profile == "kimi" and output_format.get("type") != "json_schema":
            raise _invalid("Kimi Responses supports only json_schema format configuration")
        result["text"] = {"format": output_format}
    allowed = {
        "temperature",
        "top_p",
        "parallel_tool_calls",
        "prompt_cache_key",
        "timeout",
        "metadata",
        "store",
    }
    if set(options) - allowed:
        raise _invalid("unsupported request settings: " + ", ".join(sorted(set(options) - allowed)))
    if options.get("store"):
        raise _invalid("server-side conversation storage is not supported")
    if route.parameter_profile == "deepseek":
        # Do not send settings that this route silently ignores. True parallel
        # calling and store=False match its fixed server behavior; false does not.
        if options.get("parallel_tool_calls") is False:
            raise _invalid("DeepSeek Responses cannot disable parallel tool calls")
        if any(options.get(key) is not None for key in ("metadata", "prompt_cache_key")):
            raise _invalid("DeepSeek Responses does not support metadata or prompt_cache_key")
    elif route.parameter_profile == "kimi":
        if set(options) - {"timeout", "prompt_cache_key"}:
            raise _invalid("unsupported Kimi Responses request settings")
    result.update(options)
    if route.parameter_profile == "openai":
        result["store"] = False
    return result
