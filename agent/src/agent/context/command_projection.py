"""Compact successful command diagnostics for the model, preserving raw history."""

from __future__ import annotations

import json
from dataclasses import replace

from ..messages import Message

_DIAGNOSTICS = frozenset(
    {
        "approval",
        "approval_wait_seconds",
        "launch_seconds",
        "duration_seconds",
        "host_path_validation",
        "sandbox_observation_note",
        "execution_environment",
        "launch_timings",
    }
)


def project_successful_command(message: Message) -> Message:
    if (
        message.role != "tool"
        or message.meta.get("tool_name") != "exec_command"
        or message.meta.get("is_error")
    ):
        return message
    try:
        payload, end = json.JSONDecoder().raw_decode(message.content)
    except (TypeError, ValueError):
        return message
    suffix = message.content[end:]
    projection = message.meta.get("context_projection")
    if suffix and not (
        isinstance(projection, dict)
        and projection.get("stage") == "image_budget"
        and suffix.startswith("\n[工具图像已从模型上下文移除；")
    ):
        return message
    if not isinstance(payload, dict) or not (
        payload.get("mode") == "buffered"
        and payload.get("status") == "exited"
        and type(payload.get("exit_code")) is int
        and payload["exit_code"] == 0
        and payload.get("process_status") == "exited"
        and payload.get("process_id")
        and not payload.get("error")
        and not payload.get("sandbox_denied")
        and not payload.get("dropped_bytes")
    ):
        return message
    if not _DIAGNOSTICS.intersection(payload):
        return message
    compact = {key: value for key, value in payload.items() if key not in _DIAGNOSTICS}
    compact["result_ref"] = message.uuid
    compact["diagnostics_recovery"] = "recover_tool_result"
    # Keep stdout, stderr, status, execution boundary, process handle, truncation
    # and recovery fields unchanged. In particular, exit zero is not business success.
    return replace(message, content=json.dumps(compact, ensure_ascii=False) + suffix)
