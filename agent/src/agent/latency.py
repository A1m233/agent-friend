"""Low-overhead latency tracing helpers for Agent runs and voice calls."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class RunLatencyContext:
    """Shared monotonic timeline for one runtime run.

    The context intentionally carries only correlation identifiers, counters and
    durations.  Message text, tool arguments and tool results never enter the
    latency log.
    """

    run_id: str
    session_id: str
    started_ms: int
    llm_call_count: int = 0
    context_build_count: int = 0
    tool_batch_count: int = 0
    tool_call_count: int = 0
    first_text_elapsed_ms: int | None = None

    def elapsed_ms(self) -> int:
        return max(0, monotonic_ms() - self.started_ms)

    def next_llm_call_index(self) -> int:
        self.llm_call_count += 1
        return self.llm_call_count

    def next_context_build_index(self) -> int:
        self.context_build_count += 1
        return self.context_build_count

    def next_tool_batch_index(self, call_count: int) -> int:
        self.tool_batch_count += 1
        self.tool_call_count += call_count
        return self.tool_batch_count

    def mark_first_text(self) -> bool:
        if self.first_text_elapsed_ms is not None:
            return False
        self.first_text_elapsed_ms = self.elapsed_ms()
        return True


@dataclass(frozen=True)
class VoiceLatencyContext:
    """Trace identifiers propagated from voice_bridge for one voice round."""

    trace_id: str
    call_id: str
    round_seq: int


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


def log_run_latency(
    logger: logging.Logger,
    context: RunLatencyContext | None,
    event: str,
    **fields: Any,
) -> None:
    """Log one parseable milestone on a run-wide monotonic timeline."""
    if context is None:
        return
    parts = [
        f"event={event}",
        f"run_id={context.run_id}",
        f"session_id={context.session_id}",
        f"run_elapsed_ms={context.elapsed_ms()}",
    ]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_format_value(value)}")
    logger.info("run_latency %s", " ".join(parts))


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
