"""Bounded speculative execution reads. No Conversation, hooks or tool recovery."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, RLock, Timer
from time import monotonic

from .query import CancelCheck
from .session import Session
from .store import SessionWriterLease


class PreparationCancelled(Exception):
    pass


@dataclass
class _Entry:
    cancelled: Event = field(default_factory=Event)
    state: str = "preparing"
    session: Session | None = None
    lease: SessionWriterLease | None = None
    touched: float = field(default_factory=monotonic)
    expiry: Timer | None = None


class ExecutionPreparation:
    """One speculative worker; retain at most one prepared cold session for 60s."""

    def __init__(
        self,
        loader: Callable[[str, CancelCheck], Session],
        acquire: Callable[[str], SessionWriterLease],
        current: Callable[[Session], bool],
    ) -> None:
        self._loader, self._acquire, self._current = loader, acquire, current
        self._lock = RLock()
        self._entries: dict[str, _Entry] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-prepare")
        self._closed = False
        self._running = False
        self._pending: tuple[str, _Entry] | None = None

    def _discard(self, sid: str) -> None:
        entry = self._entries.pop(sid, None)
        if entry is not None:
            entry.cancelled.set()
            if entry.expiry is not None:
                entry.expiry.cancel()
            if entry.lease is not None:
                entry.lease.release()
                entry.lease = None

    def cancel(self, sid: str) -> None:
        with self._lock:
            self._discard(sid)

    def status(self, sid: str) -> str:
        with self._lock:
            entry = self._entries.get(sid)
            if entry is not None and monotonic() - entry.touched > 60:
                self._discard(sid)
                entry = None
            if entry is not None and entry.session is not None and not self._current(entry.session):
                self._discard(sid)
                entry = None
            if entry is not None:
                entry.touched = monotonic()
            return entry.state if entry is not None else "idle"

    def request(self, sid: str) -> str:
        with self._lock:
            if self._closed:
                return "failed"
            state = self.status(sid)
            if state in {"preparing", "ready"}:
                self._entries[sid].touched = monotonic()
                return state
            for previous in tuple(self._entries):
                self._discard(previous)
            entry = _Entry()
            self._entries[sid] = entry
            self._pending = (sid, entry)
            if not self._running:
                self._running = True
                self._executor.submit(self._drain)
            return "preparing"

    def _drain(self) -> None:
        while True:
            with self._lock:
                pending, self._pending = self._pending, None
                if pending is None:
                    self._running = False
                    return
            self._load(*pending)

    def _load(self, sid: str, entry: _Entry) -> None:
        next_yield = monotonic() + 0.005

        def check() -> None:
            nonlocal next_yield
            if entry.cancelled.is_set():
                raise PreparationCancelled()
            # Speculative parsing must leave CPU/GIL time for foreground reads.
            # This is only the background path; a real send has no such throttle.
            if monotonic() >= next_yield:
                if entry.cancelled.wait(0.005):
                    raise PreparationCancelled()
                next_yield = monotonic() + 0.005

        lease = None
        try:
            check()
            lease = self._acquire(sid)
            with self._lock:
                check()
                entry.lease = lease
            session = self._loader(sid, check)
            check()
            with self._lock:
                check()
                entry.session, entry.lease, entry.state = session, lease, "ready"
                entry.touched = monotonic()
                entry.expiry = Timer(60, self._expire, args=(sid, entry))
                entry.expiry.daemon = True
                entry.expiry.start()
                lease = None
        except Exception:
            with self._lock:
                entry.state = "failed"
        finally:
            if lease is not None:
                with self._lock:
                    lease.release()
                    entry.lease = None

    def peek(self, sid: str) -> Session | None:
        with self._lock:
            if self.status(sid) != "ready":
                return None
            return self._entries[sid].session

    def _expire(self, sid: str, entry: _Entry) -> None:
        with self._lock:
            if self._entries.get(sid) is entry:
                remaining = 60 - (monotonic() - entry.touched)
                if remaining <= 0:
                    self._discard(sid)
                else:
                    entry.expiry = Timer(remaining, self._expire, args=(sid, entry))
                    entry.expiry.daemon = True
                    entry.expiry.start()

    def take(self, session: Session) -> SessionWriterLease | None:
        with self._lock:
            if self.status(session.session_id) != "ready":
                return None
            entry = self._entries[session.session_id]
            if entry.session is not session:
                return None
            self._entries.pop(session.session_id)
            if entry.expiry is not None:
                entry.expiry.cancel()
            lease, entry.lease = entry.lease, None
            return lease

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending = None
            for sid in tuple(self._entries):
                self._discard(sid)
        self._executor.shutdown(wait=True, cancel_futures=True)
