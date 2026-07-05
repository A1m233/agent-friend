"""Volc RTC callback payload decoding helpers."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecodedVolcCallback:
    """Best-effort structured view of a volatile Volc callback payload."""

    payload_type: str = ""
    stage: str = ""
    stage_code: int | None = None
    round_id: int | None = None
    event_time_ms: int | None = None
    task_id: str = ""
    user_id: str = ""
    definite: bool | None = None
    sequence: int | None = None
    text: str = ""
    item_count: int | None = None

    @property
    def text_len(self) -> int | None:
        return len(self.text) if self.text else None


def decode_volc_callback_payload(payload: Any) -> DecodedVolcCallback:
    """Decode Volc RTC callback JSON or base64 binary envelopes.

    Volc sends callbacks in at least two shapes in dev smoke:

    - plain JSON-like dictionaries
    - ``{"message": "<base64>", "binary": true}``, where the decoded bytes
      contain a short binary prefix followed by a JSON object.

    The decoder is intentionally tolerant. It returns an empty
    :class:`DecodedVolcCallback` when the payload is unknown instead of
    raising, because callback parsing must never break the voice call.
    """
    body, payload_type = _extract_payload_body(payload)
    if not isinstance(body, dict):
        return DecodedVolcCallback(payload_type=payload_type)
    return _decode_callback_dict(body, payload_type=payload_type)


def _extract_payload_body(payload: Any) -> tuple[Any, str]:
    if not isinstance(payload, dict):
        return payload, ""

    message = payload.get("message")
    if isinstance(message, str):
        decoded = _decode_binary_message(message)
        if decoded is not None:
            return decoded
        try:
            return json.loads(message), ""
        except json.JSONDecodeError:
            return payload, ""

    return payload, ""


def _decode_binary_message(message: str) -> tuple[Any, str] | None:
    try:
        raw = base64.b64decode(message, validate=True)
    except (ValueError, TypeError):
        return None

    json_start = _find_json_start(raw)
    if json_start is None:
        return None

    payload_type = _prefix_type(raw[:json_start])
    try:
        body = json.loads(raw[json_start:].decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return body, payload_type


def _find_json_start(raw: bytes) -> int | None:
    starts = [idx for marker in (b"{", b"[") if (idx := raw.find(marker)) >= 0]
    if not starts:
        return None
    return min(starts)


def _prefix_type(prefix: bytes) -> str:
    chars: list[str] = []
    for byte in prefix:
        if 65 <= byte <= 90 or 97 <= byte <= 122:
            chars.append(chr(byte).lower())
        elif chars:
            break
    return "".join(chars)


def _decode_callback_dict(data: dict[str, Any], *, payload_type: str) -> DecodedVolcCallback:
    subtitle_item, item_count = _subtitle_item(data)
    stage, stage_code = _stage(data)
    if not stage and _string(data.get("type")) == "subtitle":
        stage = "subtitle"
    if not payload_type:
        payload_type = _string(data.get("type"))

    return DecodedVolcCallback(
        payload_type=payload_type,
        stage=stage,
        stage_code=stage_code,
        round_id=_first_int(
            data.get("RoundID"),
            data.get("RoundId"),
            data.get("roundId"),
            subtitle_item.get("roundId") if subtitle_item else None,
            subtitle_item.get("roundID") if subtitle_item else None,
        ),
        event_time_ms=_first_int(
            data.get("EventTime"),
            data.get("eventTime"),
            data.get("timestamp"),
            subtitle_item.get("timestamp") if subtitle_item else None,
        ),
        task_id=_first_string(data.get("TaskId"), data.get("TaskID"), data.get("taskId")),
        user_id=_first_string(
            data.get("UserID"),
            data.get("UserId"),
            data.get("userId"),
            subtitle_item.get("userId") if subtitle_item else None,
            subtitle_item.get("UserID") if subtitle_item else None,
        ),
        definite=_bool(subtitle_item.get("definite")) if subtitle_item else None,
        sequence=_first_int(subtitle_item.get("sequence")) if subtitle_item else None,
        text=_string(subtitle_item.get("text")) if subtitle_item else "",
        item_count=item_count,
    )


def _subtitle_item(data: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    items = data.get("data")
    if not isinstance(items, list):
        return None, None
    dict_items = [item for item in items if isinstance(item, dict)]
    if not dict_items:
        return None, len(items)
    return dict_items[-1], len(items)


def _stage(data: dict[str, Any]) -> tuple[str, int | None]:
    raw_stage = data.get("Stage") or data.get("stage")
    if isinstance(raw_stage, dict):
        return (
            _first_string(raw_stage.get("Description"), raw_stage.get("description")),
            _first_int(raw_stage.get("Code"), raw_stage.get("code")),
        )
    return _string(raw_stage), None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return int(value)
            except ValueError:
                continue
    return None


def _first_string(*values: Any) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
