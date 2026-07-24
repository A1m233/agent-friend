"""Per-session turn coordination for persistent bridge channels."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from agent.runtime import SessionBusyError


class SessionTurnCoordinator:
    """Serialize mutating turns for the same persistent session.

    The JSONL store protects individual appends, but a user-visible turn spans
    multiple reads, LLM streaming steps, and appends. This coordinator keeps
    those compound operations ordered per session while allowing different
    sessions to run concurrently.
    """

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, Lock] = {}

    @contextmanager
    def hold(self, session_id: str) -> Iterator[None]:
        lock = self._lock_for(session_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def try_hold(self, session_id: str) -> Iterator[None]:
        """Acquire the session turn lock without waiting behind an active run."""

        lock = self._lock_for(session_id)
        if not lock.acquire(blocking=False):
            raise SessionBusyError(f"session {session_id!r} is busy")
        try:
            yield
        finally:
            lock.release()

    def _lock_for(self, session_id: str) -> Lock:
        with self._guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = Lock()
                self._locks[session_id] = lock
            return lock
