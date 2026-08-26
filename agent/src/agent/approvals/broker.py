"""Thread-safe shared broker for typed, one-shot approval requests."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol, cast

from .models import (
    ENABLED_APPROVAL_LIFETIMES,
    ApprovalDecisionLike,
    SessionApprovalMode,
    SessionApprovalModeState,
)
from .risk import CatastrophicActionPolicy

logger = logging.getLogger(__name__)


class ApprovalModeStore(Protocol):
    def get(self, session_id: str) -> Any: ...

    def compare_and_set_approval_mode(
        self,
        session_id: str,
        *,
        expected_version: int,
        mode: SessionApprovalMode,
    ) -> tuple[SessionApprovalModeState, bool]: ...


class ApprovalModeConflictError(RuntimeError):
    def __init__(self, current: SessionApprovalModeState) -> None:
        super().__init__("session approval mode changed")
        self.current = current


class ApprovalRequestStaleError(RuntimeError):
    pass


class ApprovalBroker:
    """Own the lifecycle and routing of every local capability approval.

    Resource-specific request types still own their exact decision payloads.
    This broker is the single control plane for pending state, session-mode
    routing, cancellation and notifications, so command and filesystem paths
    cannot drift into parallel state machines.
    """

    def __init__(
        self,
        *,
        mode_store: ApprovalModeStore | None = None,
        risk_policy: CatastrophicActionPolicy | None = None,
        session_exists: Callable[[str], bool] | None = None,
        on_mode_changed: Callable[[str, SessionApprovalModeState], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, Any] = {}
        self._listeners: list[Callable[[dict[str, object]], None]] = []
        self._session_locks: dict[str, threading.RLock] = {}
        self._mode_store = mode_store
        self._risk_policy = risk_policy
        self._session_exists = session_exists
        self._on_mode_changed = on_mode_changed

    def add_listener(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def mode_state(self, session_id: str) -> SessionApprovalModeState:
        store = self._mode_store
        if store is None:
            return SessionApprovalModeState()
        metadata = store.get(session_id)
        return SessionApprovalModeState(
            getattr(metadata, "approval_mode", "per_request"),
            getattr(metadata, "approval_mode_version", 0),
        )

    def reassess(self, request: Any) -> Any | None:
        """Re-run the deterministic policy immediately before a side effect."""

        if self._risk_policy is None:
            return None
        return self._risk_policy.assess(request)

    def set_session_mode(
        self,
        session_id: str,
        *,
        mode: SessionApprovalMode,
        expected_version: int,
    ) -> SessionApprovalModeState:
        lock = self._session_lock(session_id)
        changed = False
        resolved: tuple[Any, ...] = ()
        listeners: tuple[Callable[[dict[str, object]], None], ...] = ()
        with lock:
            self._require_session(session_id)
            state, changed = self._set_mode_locked(
                session_id,
                mode=mode,
                expected_version=expected_version,
            )
            if changed and state.mode == "session_auto":
                with self._lock:
                    resolved = self._resolve_pending_for_session_auto_locked(
                        session_id,
                        state,
                    )
                    listeners = tuple(self._listeners)
        if changed:
            self._notify_mode_changed(session_id, state)
        for request in resolved:
            self._notify(listeners, request)
        return state

    def request(self, request: Any, *, timeout_seconds: float) -> Any:
        if request.requested_lifetime not in ENABLED_APPROVAL_LIFETIMES:
            raise ValueError(f"approval lifetime is not enabled: {request.requested_lifetime}")
        assessment = self._risk_policy.assess(request) if self._risk_policy is not None else None
        lock = self._session_lock(request.session_id)
        immediate = False
        with lock:
            mode_state = self.mode_state(request.session_id)
            request.approval_mode = mode_state.mode
            request.approval_mode_version = mode_state.version
            request.risk_assessment = assessment
            if mode_state.mode == "session_auto":
                immediate = True
                if assessment is not None and assessment.catastrophic:
                    decision = request.cancelled_decision()
                    request.decision_source = "catastrophic_policy"
                    request.status = "denied"
                else:
                    decision = request.fully_approved_decision()
                    request.decision_source = "session_auto"
                    request.status = "approved"
                request._decision = decision
                request.version += 1
                request._event.set()
                with self._lock:
                    listeners = tuple(self._listeners)
            else:
                with self._lock:
                    if request.request_id in self._pending:
                        raise ValueError(f"duplicate approval request: {request.request_id}")
                    self._pending[request.request_id] = request
                    listeners = tuple(self._listeners)
        self._notify(listeners, request)
        if immediate:
            return request._decision

        if not request._event.wait(timeout_seconds):
            with lock, self._lock:
                if request._event.is_set():
                    self._pending.pop(request.request_id, None)
                    return request._decision
                self._pending.pop(request.request_id, None)
                request.status = "expired"
                request.version += 1
                listeners = tuple(self._listeners)
            self._notify(listeners, request)
            return None
        with lock, self._lock:
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
        effective_session_id = session_id or self._pending_session_id(request_id)
        if effective_session_id is None:
            return False
        lock = self._session_lock(effective_session_id)
        with lock, self._lock:
            request = self._pending.get(request_id)
            if request is None or not self._matches_pending(
                request,
                session_id=session_id,
                run_id=run_id,
                expected_kind=expected_kind,
                lifetime=lifetime,
            ):
                return False
            request._decision = decision
            request.decision_source = "user_once"
            request.status = "approved" if decision.approved else "denied"
            request.version += 1
            request._event.set()
            listeners = tuple(self._listeners)
        self._notify(listeners, request)
        return True

    def decide_and_enable_session_auto(
        self,
        request_id: str,
        *,
        session_id: str,
        run_id: str,
        expected_request_version: int,
        expected_mode_version: int,
        precondition: Callable[[Any], None] | None = None,
    ) -> SessionApprovalModeState:
        lock = self._session_lock(session_id)
        resolved: tuple[Any, ...] = ()
        with lock:
            self._require_session(session_id)
            with self._lock:
                request = self._pending.get(request_id)
                if (
                    request is None
                    or not self._matches_pending(
                        request,
                        session_id=session_id,
                        run_id=run_id,
                        expected_kind=None,
                        lifetime="once",
                    )
                    or request.version != expected_request_version
                ):
                    raise ApprovalRequestStaleError("approval request is no longer pending")
            assert request is not None
            pending_request = cast(Any, request)
            if precondition is not None:
                precondition(pending_request)
            state, changed = self._set_mode_locked(
                session_id,
                mode="session_auto",
                expected_version=expected_mode_version,
            )
            with self._lock:
                current = self._pending.get(request_id)
                if (
                    current is None
                    or current is not pending_request
                    or pending_request._event.is_set()
                ):
                    raise ApprovalRequestStaleError("approval request changed during transition")
                pending_request._decision = pending_request.fully_approved_decision()
                pending_request.decision_source = "user_enable_session_auto"
                pending_request.status = "approved"
                pending_request.approval_mode = state.mode
                pending_request.approval_mode_version = state.version
                pending_request.version += 1
                pending_request._event.set()
                resolved = self._resolve_pending_for_session_auto_locked(
                    session_id,
                    state,
                    exclude_request_id=request_id,
                )
                listeners = tuple(self._listeners)
        self._notify(listeners, pending_request)
        for request in resolved:
            self._notify(listeners, request)
        if changed:
            self._notify_mode_changed(session_id, state)
        return state

    def cancel_run(self, session_id: str, run_id: str) -> None:
        self._cancel_session_requests(session_id, lambda item: item.run_id == run_id)

    def deny_session(self, session_id: str) -> None:
        """Compatibility entrypoint; new cancellation paths should use cancel_run."""

        self._cancel_session_requests(session_id, lambda _item: True)

    def _cancel_session_requests(
        self,
        session_id: str,
        predicate: Callable[[Any], bool],
    ) -> None:
        lock = self._session_lock(session_id)
        with lock, self._lock:
            requests = [
                request
                for request in self._pending.values()
                if request.session_id == session_id
                and predicate(request)
                and not request._event.is_set()
            ]
            for request in requests:
                request._decision = request.cancelled_decision()
                request.status = "cancelled"
                request.version += 1
                request._event.set()
            listeners = tuple(self._listeners)
        for request in requests:
            self._notify(listeners, request)

    def _set_mode_locked(
        self,
        session_id: str,
        *,
        mode: SessionApprovalMode,
        expected_version: int,
    ) -> tuple[SessionApprovalModeState, bool]:
        store = self._mode_store
        if store is None:
            raise RuntimeError("session approval mode store is unavailable")
        current = self.mode_state(session_id)
        if current.version != expected_version:
            raise ApprovalModeConflictError(current)
        if current.mode == mode:
            return current, False
        state, changed = store.compare_and_set_approval_mode(
            session_id,
            expected_version=expected_version,
            mode=mode,
        )
        if not changed:
            if state.mode == mode and state.version == expected_version:
                return state, False
            raise ApprovalModeConflictError(state)
        return state, True

    def _resolve_pending_for_session_auto_locked(
        self,
        session_id: str,
        state: SessionApprovalModeState,
        *,
        exclude_request_id: str | None = None,
    ) -> tuple[Any, ...]:
        """Resolve pre-existing pending requests after an acknowledged auto-mode switch.

        The caller holds both the per-session lock and ``self._lock``.  Normal
        requests receive their exact full decision; catastrophic requests are
        denied by the same deterministic policy used for newly-created requests.
        """

        resolved = tuple(
            request
            for request in self._pending.values()
            if request.session_id == session_id
            and request.request_id != exclude_request_id
            and not request._event.is_set()
        )
        for request in resolved:
            assessment = request.risk_assessment
            if assessment is not None and assessment.catastrophic:
                request._decision = request.cancelled_decision()
                request.decision_source = "catastrophic_policy"
                request.status = "denied"
            else:
                request._decision = request.fully_approved_decision()
                request.decision_source = "session_auto"
                request.status = "approved"
            request.approval_mode = state.mode
            request.approval_mode_version = state.version
            request.version += 1
            request._event.set()
        return resolved

    def _require_session(self, session_id: str) -> None:
        if self._session_exists is not None and not self._session_exists(session_id):
            raise KeyError(session_id)

    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._lock:
            return self._session_locks.setdefault(session_id, threading.RLock())

    def _pending_session_id(self, request_id: str) -> str | None:
        with self._lock:
            request = self._pending.get(request_id)
            return request.session_id if request is not None else None

    @staticmethod
    def _matches_pending(
        request: Any | None,
        *,
        session_id: str | None,
        run_id: str | None,
        expected_kind: str | None,
        lifetime: str,
    ) -> bool:
        return bool(
            request is not None
            and not request._event.is_set()
            and (session_id is None or request.session_id == session_id)
            and (run_id is None or request.run_id == run_id)
            and (expected_kind is None or request.kind == expected_kind)
            and request.requested_lifetime == lifetime
        )

    def _notify_mode_changed(
        self,
        session_id: str,
        state: SessionApprovalModeState,
    ) -> None:
        callback = self._on_mode_changed
        if callback is None:
            return
        try:
            callback(session_id, state)
        except Exception:
            logger.warning("approval mode listener failed", exc_info=True)

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


__all__ = [
    "ApprovalBroker",
    "ApprovalModeConflictError",
    "ApprovalRequestStaleError",
]
