"""Content-free categories for diagnosing a live provider stream, not timeout policy."""

from __future__ import annotations

from typing import Any


def _field(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def progress_category(chunk: Any) -> tuple[str, bool]:
    """Only emit fixed labels; unknown SDK events are not assumed to be empty."""
    kind = _field(chunk, "type")
    if isinstance(kind, str):
        deltas = {
            "response.output_text.delta": "text",
            "response.function_call_arguments.delta": "tool_arguments",
            "response.reasoning_text.delta": "reasoning",
            "response.reasoning_summary_text.delta": "reasoning",
        }
        if kind in deltas:
            return (deltas[kind], True) if _field(chunk, "delta") else ("empty_delta", False)
        if kind in {"response.completed", "response.failed", "response.incomplete", "error"}:
            return "terminal", True
        if kind in {
            "response.output_item.added",
            "response.output_item.done",
            "response.content_part.added",
            "response.content_part.done",
        }:
            return "item", True
        if kind in {"ping", "keepalive", "heartbeat"}:
            return "heartbeat", False
        if kind in {"response.created", "response.in_progress"}:
            return "metadata", False
    choices = _field(chunk, "choices")
    if isinstance(choices, list):
        for choice in choices:
            if _field(choice, "finish_reason"):
                return "terminal", True
            delta = _field(choice, "delta")
            if _field(delta, "content"):
                return "text", True
            if _field(delta, "reasoning_content") or _field(delta, "reasoning"):
                return "reasoning", True
            if _field(delta, "tool_calls"):
                return "tool_arguments", True
        return "empty_delta", False
    return "unclassified", False
