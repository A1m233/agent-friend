"""Small presentation facts stored separately from potentially large event bodies."""

from __future__ import annotations

import json
from typing import Any

from ..events import Event

DISPLAY_TYPES = frozenset(
    {
        "user_message",
        "assistant_message",
        "tool_call_request",
        "todo_reminder",
        "compaction",
        "skill_context",
        "run_error",
    }
)


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def event_dict(event: Event) -> dict[str, Any]:
    return {
        "type": event.type,
        "uuid": event.uuid,
        "ts": event.ts.isoformat(),
        "payload": dict(event.payload),
        "meta": dict(event.meta),
    }


def small_fields(value: dict[str, Any], *, budget: int = 4096) -> dict[str, Any]:
    """Keep small scalar facts; never pretend a truncated body is complete."""
    excluded = {"content", "text", "arguments_json", "args", "content_parts", "data"}
    result: dict[str, Any] = {}
    used = 2
    for key, item in value.items():
        if key in excluded:
            continue
        encoded = dump({key: item})
        size = len(encoded.encode("utf-8"))
        if size <= 1024 and used + size <= budget:
            result[key] = item
            used += size
    return result


def summary(event: Event) -> dict[str, Any]:
    result = event_dict(event)
    result["payload"] = small_fields(result["payload"])
    result["meta"] = small_fields(result["meta"], budget=2048)
    if event.type == "tool_call_request":
        arguments = event.payload.get("args")
        if not isinstance(arguments, dict):
            try:
                arguments = json.loads(str(event.payload.get("arguments_json", "")))
            except (ValueError, TypeError):
                arguments = None
        if isinstance(arguments, dict):
            result["summary"] = small_fields(arguments, budget=2048)
    # Tools commonly wrap status/approval facts in a JSON result string.
    content = event.payload.get("content")
    if event.type == "tool_call_result" and isinstance(content, str):
        try:
            body = json.loads(content)
        except (ValueError, TypeError):
            body = None
        if isinstance(body, dict):
            result["summary"] = small_fields(body, budget=2048)
        if content.startswith("[error]"):
            result["payload"]["is_error"] = True
    return result


def run_id(event: Event) -> str:
    return str(
        event.meta.get("run_id")
        or event.payload.get("run_id")
        or (f"legacy:{event.uuid}" if event.type == "run_error" else "")
    )


def tool_key(event: Event) -> str:
    return str(event.payload.get("event_tool_call_id") or event.payload.get("tool_call_id") or "")
