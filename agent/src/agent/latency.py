"""Low-overhead latency tracing helpers for Agent runs and voice calls."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
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
    phase_count: int = 0

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
    elapsed_ms = context.elapsed_ms()
    parts = [
        f"event={event}",
        f"run_id={context.run_id}",
        f"session_id={context.session_id}",
        f"run_elapsed_ms={elapsed_ms}",
    ]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_format_value(value)}")
    logger.info(
        "run_latency %s",
        " ".join(parts),
        extra={
            "run_latency": {
                "event": event,
                "run_id": context.run_id,
                "session_id": context.session_id,
                "run_elapsed_ms": elapsed_ms,
                **fields,
            }
        },
    )


# Context-local nesting also lets offline collectors attribute existing file and
# memory-source records without making those lower-level packages depend on Agent.
_current_phase: ContextVar[tuple[str, int] | None] = ContextVar("run_phase", default=None)


def current_run_phase() -> tuple[str, int] | None:
    return _current_phase.get()


@contextmanager
def observe_run_phase(
    logger: logging.Logger, context: RunLatencyContext | None, phase: str
) -> Iterator[None]:
    """Measure a synchronous phase without changing its outcome or logging content."""
    if context is None:
        yield
        return
    context.phase_count += 1
    phase_id = context.phase_count
    parent = _current_phase.get()
    parent_id = parent[1] if parent is not None and parent[0] == context.run_id else None
    started, cpu_started = time.perf_counter(), time.thread_time()
    started_elapsed_ms = context.elapsed_ms()
    token = _current_phase.set((context.run_id, phase_id))
    status, error_type = "ok", None
    with suppress(Exception):
        log_run_latency(
            logger,
            context,
            "prepare_phase_start",
            phase=phase,
            phase_id=phase_id,
            parent_phase_id=parent_id,
        )
    try:
        yield
    except BaseException as exc:
        status, error_type = "error", type(exc).__name__
        with suppress(Exception):
            if getattr(getattr(exc, "cancellation", None), "reason", None) is not None:
                status = "cancelled"
        raise
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        cpu_ms = (time.thread_time() - cpu_started) * 1000
        _current_phase.reset(token)
        with suppress(Exception):
            log_run_latency(
                logger,
                context,
                "prepare_phase_done",
                phase=phase,
                phase_id=phase_id,
                parent_phase_id=parent_id,
                started_elapsed_ms=started_elapsed_ms,
                duration_ms=round(duration_ms, 3),
                thread_cpu_ms=round(cpu_ms, 3),
                status=status,
                error_type=error_type,
            )


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
