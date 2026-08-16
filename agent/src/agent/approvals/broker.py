"""Thread-safe shared broker for typed, one-shot approval requests."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from .models import (
    ENABLED_APPROVAL_LIFETIMES,
    ApprovalDecisionLike,
)

logger = logging.getLogger(__name__)


class ApprovalBroker:
    """Own the lifecycle of every active local capability approval.

    Resource-specific adapters remain responsible for parsing and validating their
    decision payloads.  The broker owns only correlation, lifetime, terminal state,
    waiting and notifications, so filesystem and command approvals cannot drift into
    parallel state machines.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Typed adapters validate their own request and decision variants.  The shared
        # registry is intentionally heterogeneous, so its storage boundary uses Any.
        self._pending: dict[str, Any] = {}
        self._listeners: list[Callable[[dict[str, object]], None]] = []

    def add_listener(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def request(self, request: Any, *, timeout_seconds: float) -> Any:
        if request.requested_lifetime not in ENABLED_APPROVAL_LIFETIMES:
            raise ValueError(f"approval lifetime is not enabled: {request.requested_lifetime}")
        with self._lock:
            if request.request_id in self._pending:
                raise ValueError(f"duplicate approval request: {request.request_id}")
            self._pending[request.request_id] = request
            listeners = tuple(self._listeners)
        self._notify(listeners, request)
        if not request._event.wait(timeout_seconds):
            with self._lock:
                self._pending.pop(request.request_id, None)
                request.status = "expired"
                request.version += 1
                listeners = tuple(self._listeners)
            self._notify(listeners, request)
            return None
        with self._lock:
            self._pending.pop(request.request_id, None)
        return request._decision

    def pending(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        kind: str | None = None,
    ) -> tuple[Any, ...]:
        with self._lock:
            return tuple(
                request
                for request in self._pending.values()
                if (session_id is None or request.session_id == session_id)
                and (run_id is None or request.run_id == run_id)
                and (kind is None or request.kind == kind)
                and not request._event.is_set()
            )

    def pending_for_session(self, session_id: str) -> tuple[Any, ...]:
        return self.pending(session_id=session_id)

    def decide(
        self,
        request_id: str,
        decision: ApprovalDecisionLike,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        expected_kind: str | None = None,
        lifetime: str = "once",
    ) -> bool:
        if lifetime not in ENABLED_APPROVAL_LIFETIMES:
            return False
        with self._lock:
            request = self._pending.get(request_id)
            if request is None or request._event.is_set():
                return False
            if session_id is not None and request.session_id != session_id:
                return False
            if run_id is not None and request.run_id != run_id:
                return False
            if expected_kind is not None and request.kind != expected_kind:
                return False
            if request.requested_lifetime != lifetime:
                return False
            request._decision = decision
            request.status = "approved" if decision.approved else "denied"
            request.version += 1
            request._event.set()
            listeners = tuple(self._listeners)
        self._notify(listeners, request)
        return True

    def cancel_run(self, session_id: str, run_id: str) -> None:
        self._cancel_matching(lambda item: item.session_id == session_id and item.run_id == run_id)

    def deny_session(self, session_id: str) -> None:
        """Compatibility entrypoint; new cancellation paths should use cancel_run."""

        self._cancel_matching(lambda item: item.session_id == session_id)

    def _cancel_matching(self, predicate: Callable[[Any], bool]) -> None:
        with self._lock:
            requests = [
                request
                for request in self._pending.values()
                if predicate(request) and not request._event.is_set()
            ]
            for request in requests:
                request._decision = request.cancelled_decision()
                request.status = "cancelled"
                request.version += 1
                request._event.set()
            listeners = tuple(self._listeners)
        for request in requests:
            self._notify(listeners, request)

    @staticmethod
    def _notify(
        listeners: tuple[Callable[[dict[str, object]], None], ...],
        request: Any,
    ) -> None:
        payload = request.to_dict()
        for listener in listeners:
            try:
                listener(payload)
            except Exception:
                logger.warning("approval listener failed", exc_info=True)
