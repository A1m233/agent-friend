"""Content-free summaries at the Responses parser boundary."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Any

from ..errors import ProtocolEvidence

REASONING_EVENTS = frozenset(
    f"response.{family}.{suffix}"
    for family, suffixes in (
        ("reasoning_summary_part", ("added", "done")),
        ("reasoning_summary_text", ("delta", "done")),
        ("reasoning_text", ("delta", "done")),
    )
    for suffix in suffixes
)
REFUSAL_EVENTS = frozenset({"response.refusal.delta", "response.refusal.done"})
UNSUPPORTED_EVENTS = frozenset(
    f"response.{family}.{suffix}"
    for family, suffixes in (
        ("audio", ("delta", "done")),
        ("audio.transcript", ("delta", "done")),
        ("code_interpreter_call_code", ("delta", "done")),
        ("code_interpreter_call", ("in_progress", "interpreting", "completed")),
        ("file_search_call", ("in_progress", "searching", "completed")),
        ("web_search_call", ("in_progress", "searching", "completed")),
        ("image_generation_call", ("in_progress", "generating", "partial_image", "completed")),
        ("mcp_call_arguments", ("delta", "done")),
        ("mcp_call", ("in_progress", "completed", "failed")),
        ("mcp_list_tools", ("in_progress", "completed", "failed")),
        ("custom_tool_call_input", ("delta", "done")),
        ("shell_call_command", ("added", "delta", "done")),
        ("shell_call_output_content", ("delta", "done")),
    )
    for suffix in suffixes
)
STRUCTURAL_FIELDS = (
    "type",
    "sequence_number",
    "response",
    "item",
    "item_id",
    "output_index",
    "content_index",
    "annotation_index",
    "annotation",
    "part",
    "delta",
    "text",
    "arguments",
    "summary_index",
    "error",
    "code",
    "message",
)


def reference(value: str | None) -> str | None:
    return sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24] if value else None


def event_type(value: Any) -> str:
    if (
        isinstance(value, str)
        and len(value) <= 96
        and re.fullmatch(r"(?:response\.[a-z_]+(?:\.[a-z_]+)*|error)", value)
    ):
        return value
    return "<redacted-or-missing>"


def safe_index(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**31 - 1 else None


def part_type(event: dict[str, Any] | None) -> str | None:
    part = event.get("part") if event else None
    if not isinstance(part, dict):
        return None
    kind = part.get("type")
    return (
        kind
        if isinstance(kind, str)
        and kind in {"output_text", "reasoning_text", "summary_text", "refusal"}
        else "<redacted-or-unknown>"
    )


def _shape(value: Any) -> str:
    # Do not use arbitrary class names or repr: both may contain private data.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "other"


def summarize(
    event: dict[str, Any] | None, *, stage: str, number: int, items: int, terminal: bool
) -> ProtocolEvidence:
    kind = event.get("type") if event is not None else None
    state = "missing" if event is None or "type" not in event else "invalid"
    safe_type = None
    digest = None
    if isinstance(kind, str):
        # No arbitrary provider strings in logs. Unknown protocol-shaped names
        # remain useful, but credentials, free text and oversized values do not.
        digest = reference(kind)
        if len(kind) <= 96 and re.fullmatch(r"(?:response\.[a-z_]+(?:\.[a-z_]+)*|error)", kind):
            safe_type, state = kind, "recorded"
        else:
            state = "redacted"
    fields = tuple((key, _shape(event[key])) for key in STRUCTURAL_FIELDS if event and key in event)
    unknown = len(event) - len(fields) if event is not None else 0
    output: list[Any] = []
    if event is not None:
        if "item" in event:
            output = [event["item"]]
        elif isinstance(event.get("response"), dict):
            candidate = event["response"].get("output")
            if isinstance(candidate, list):
                output = candidate
    kinds = []
    for item in output[:8]:
        value = item.get("type") if isinstance(item, dict) else None
        kinds.append(
            value
            if isinstance(value, str) and len(value) <= 48 and re.fullmatch(r"[a-z_]+", value)
            else "<redacted-or-missing>"
        )
    return ProtocolEvidence(
        stage,
        number,
        safe_type,
        state,
        digest,
        fields,
        unknown,
        items,
        terminal,
        tuple(kinds),
        len(output) > 8,
    )
