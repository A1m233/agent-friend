"""Content-free timing for the session JSONL lock and its associated operation."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from portalocker.exceptions import LockException

logger = logging.getLogger(__name__)
_SLOW_LOCK_SECONDS = 0.1


@dataclass
class SessionFileObservation:
    operation: Literal["load", "create", "append"]
    session_id: str
    lock_acquired: bool = False
    wait_seconds: float = 0.0
    hold_seconds: float | None = None
    parse_seconds: float | None = None
    byte_count: int = 0
    outcome: str = "success"
    error_type: str | None = None

    @contextmanager
    def locked(self, lock: AbstractContextManager[object]) -> Iterator[object]:
        started = perf_counter()
        acquired: float | None = None
        try:
            with lock as handle:
                acquired = perf_counter()
                self.lock_acquired = True
                self.wait_seconds = acquired - started
                yield handle
        finally:
            finished = perf_counter()
            if acquired is None:
                self.wait_seconds = finished - started
            else:
                # Includes release/close; no logger runs until this scope has exited.
                self.hold_seconds = finished - acquired

    @contextmanager
    def parsing(self) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self.parse_seconds = perf_counter() - started

    def emit(self) -> None:
        level = logging.DEBUG
        if self.outcome != "success":
            level = logging.WARNING
        elif max(self.wait_seconds, self.hold_seconds or 0.0) >= _SLOW_LOCK_SECONDS:
            level = logging.INFO
        # Logical session ids may be user-supplied. A stable digest permits local
        # correlation without logging arbitrary id text, paths or event content.
        session_key = hashlib.sha256(
            self.session_id.encode("utf-8", errors="surrogatepass")
        ).hexdigest()[:16]
        fields = {
            "session_file_operation": self.operation,
            "session_key": session_key,
            "process_id": os.getpid(),
            "thread_id": threading.get_native_id(),
            "lock_acquired": self.lock_acquired,
            "wait_ms": round(self.wait_seconds * 1000, 3),
            "hold_ms": (
                round(self.hold_seconds * 1000, 3) if self.hold_seconds is not None else None
            ),
            "parse_ms": (
                round(self.parse_seconds * 1000, 3) if self.parse_seconds is not None else None
            ),
            "byte_count": self.byte_count,
            "outcome": self.outcome,
            "error_type": self.error_type,
        }
        logger.log(
            level,
            "session_file operation=%s session_key=%s pid=%s thread=%s "
            "lock_acquired=%s wait_ms=%s hold_ms=%s parse_ms=%s bytes=%s "
            "outcome=%s error_type=%s",
            *fields.values(),
            extra=fields,
        )


@contextmanager
def observe_session_file(
    operation: Literal["load", "create", "append"], session_id: str
) -> Iterator[SessionFileObservation]:
    observation = SessionFileObservation(operation, session_id)
    try:
        yield observation
    except BaseException as exc:
        observation.outcome = (
            "lock_timeout"
            if isinstance(exc, LockException) and not observation.lock_acquired
            else "error"
        )
        observation.error_type = type(exc).__name__
        raise
    finally:
        # Neither field construction nor a logging/handler failure may change
        # a successful operation or replace its original storage exception.
        with suppress(Exception):
            observation.emit()
