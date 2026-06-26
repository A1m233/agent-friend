"""Optional latency trace context for voice calls."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VoiceLatencyContext:
    """Trace identifiers propagated from voice_bridge for one voice round."""

    trace_id: str
    call_id: str
    round_seq: int


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


def log_voice_latency(
    logger: logging.Logger,
    context: VoiceLatencyContext | None,
    event: str,
    **fields: Any,
) -> None:
    """Log a voice latency event when a voice context is present."""
    if context is None:
        return
    parts = [
        f"event={event}",
        f"trace_id={context.trace_id}",
        f"call_id={context.call_id}",
        f"round_seq={context.round_seq}",
    ]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_format_value(value)}")
    logger.info("voice_latency %s", " ".join(parts))


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text or any(ch.isspace() for ch in text):
        return json.dumps(text, ensure_ascii=False)
    return text
