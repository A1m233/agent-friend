"""Thread-safe broker for one-shot desktop filesystem approvals."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .models import ApprovalDecision, PendingFilesystemApproval

logger = logging.getLogger(__name__)


class FilesystemApprovalBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingFilesystemApproval] = {}
        self._listeners: list[Callable[[dict[str, object]], None]] = []

    def add_listener(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def request(
        self, request: PendingFilesystemApproval, *, timeout_seconds: float
    ) -> ApprovalDecision | None:
        with self._lock:
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

    def pending_for_session(self, session_id: str) -> tuple[PendingFilesystemApproval, ...]:
        return self.pending(session_id=session_id)

    def pending(self, *, session_id: str | None = None) -> tuple[PendingFilesystemApproval, ...]:
        with self._lock:
            return tuple(
                request
                for request in self._pending.values()
                if (session_id is None or request.session_id == session_id)
                and not request._event.is_set()
            )

    def decide(
        self,
        request_id: str,
        decision: ApprovalDecision,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> bool:
        with self._lock:
            request = self._pending.get(request_id)
            if request is None or request._event.is_set():
                return False
            if session_id is not None and request.session_id != session_id:
                return False
            if run_id is not None and request.run_id != run_id:
                return False
            request._decision = decision
            request.status = (
                "approved"
                if decision.approved_permissions or decision.approved_action_ids
                else "denied"
            )
            request.version += 1
            request._event.set()
            listeners = tuple(self._listeners)
        self._notify(listeners, request)
        return True

    def deny_session(self, session_id: str) -> None:
        with self._lock:
            requests = [
                request
                for request in self._pending.values()
                if request.session_id == session_id and not request._event.is_set()
            ]
            for request in requests:
                request._decision = ApprovalDecision()
                request.status = "cancelled"
                request.version += 1
                request._event.set()
            listeners = tuple(self._listeners)
        for request in requests:
            self._notify(listeners, request)

    @staticmethod
    def _notify(
        listeners: tuple[Callable[[dict[str, object]], None], ...],
        request: PendingFilesystemApproval,
    ) -> None:
        payload = request.to_dict()
        for listener in listeners:
            try:
                listener(payload)
            except Exception:
                logger.warning("filesystem approval listener failed", exc_info=True)
