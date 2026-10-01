"""Content-free query-cache lifecycle observations, separate from file-read counts."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

logger = logging.getLogger(__name__)
_SLOW_QUERY_SECONDS = 0.1


@dataclass
class SessionQueryObservation:
    session_id: str
    projection_kind: Literal["business", "recall"] = "business"
    action: Literal["lookup", "waiting", "hit", "coalesced", "rebuilt", "invalidated"] = "lookup"
    miss_reason: Literal["absent", "source_changed", "maintenance_failed", "missing"] | None = None
    wait_seconds: float = 0.0
    published: bool | None = None
    publish_reason: Literal["stored", "source_changed", "capacity"] | None = None
    outcome: Literal["success", "cancelled", "error"] = "success"
    error_type: str | None = None
    started: float = field(default_factory=perf_counter)

    def emit(self) -> None:
        elapsed = perf_counter() - self.started
        level = logging.DEBUG
        if self.outcome == "error":
            level = logging.WARNING
        elif self.outcome == "cancelled" or max(elapsed, self.wait_seconds) >= _SLOW_QUERY_SECONDS:
            level = logging.INFO
        fields = {
            "session_key": hashlib.sha256(
                self.session_id.encode("utf-8", errors="surrogatepass")
            ).hexdigest()[:16],
            "process_id": os.getpid(),
            "thread_id": threading.get_native_id(),
            "cache_action": self.action,
            "cache_miss_reason": self.miss_reason,
            "query_wait_ms": round(self.wait_seconds * 1000, 3),
            "query_total_ms": round(elapsed * 1000, 3),
            "cache_published": self.published,
            "cache_publish_reason": self.publish_reason,
            "outcome": self.outcome,
            "error_type": self.error_type,
            "projection_kind": self.projection_kind,
        }
        logger.log(
            level,
            "session_query session_key=%s pid=%s thread=%s action=%s miss_reason=%s "
            "wait_ms=%s total_ms=%s published=%s publish_reason=%s outcome=%s error_type=%s "
            "projection=%s",
            *fields.values(),
            extra=fields,
        )


@contextmanager
def observe_session_query(
    session_id: str,
    *,
    projection_kind: Literal["business", "recall"] = "business",
) -> Iterator[SessionQueryObservation]:
    observation = SessionQueryObservation(session_id, projection_kind=projection_kind)
    try:
        yield observation
    except BaseException as exc:
        # Match the shared cancellation contract without importing agent runtime.
        observation.outcome = "error"
        with suppress(Exception):
            cancellation = getattr(exc, "cancellation", None)
            if getattr(cancellation, "reason", None) is not None:
                observation.outcome = "cancelled"
        observation.error_type = type(exc).__name__
        raise
    finally:
        # Logging handlers and telemetry formatting cannot change business outcomes.
        with suppress(Exception):
            observation.emit()


def observe_query_maintenance_failure(session_id: str, error: Exception) -> None:
    with suppress(Exception):
        SessionQueryObservation(
            session_id,
            action="invalidated",
            miss_reason="maintenance_failed",
            published=False,
            outcome="error",
            error_type=type(error).__name__,
        ).emit()
