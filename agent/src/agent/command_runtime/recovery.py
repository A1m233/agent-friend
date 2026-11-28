"""One bounded recovery flight shared by callers of a command runtime.

The coordinator never replays an ambiguous action. The owner supplies readiness,
quiescence and a narrow rejection classifier; ordinary business failures pass through.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from ..tool_runtime.control import (
    RECOVERY_WAIT_SECONDS,
    ToolInvocationCancelled,
    ToolInvocationControl,
)

RECOVERY_TIMEOUT_SECONDS = RECOVERY_WAIT_SECONDS
RECOVERY_COOLDOWN_SECONDS = 60.0
SETUP_TIMEOUT_SECONDS = 300.0
LEGACY_SETUP_WAIT_SECONDS = 20.0
T = TypeVar("T")


class RecoveryUnavailable(RuntimeError):
    pass


class RecoveryBlocked(RecoveryUnavailable):
    def __init__(self, blockers: list[dict[str, Any]]) -> None:
        super().__init__("sandbox recovery blocked: earlier command resources are unconfirmed")
        self.blockers = blockers


@dataclass
class _Caller:
    control: ToolInvocationControl
    validate: Callable[[], None]
    used: bool = False
    operation: str = ""


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    waiters: int = 0
    error: BaseException | None = None
    setup_timeout: float = 300.0
    operation: str = field(default_factory=lambda: uuid4().hex)
    stage: str = "automatic_recovery"
    phase: str = "reconciling"
    result: str | None = None
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deadline: float = 0
    retained: bool = False
    cancelled: bool = False
    request_keys: list[str] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)


class SandboxRecovery:
    def __init__(
        self,
        *,
        ready: Callable[[], bool],
        idle: Callable[[], bool],
        repair: Callable[[Callable[[], None]], object],
        retryable: Callable[[Exception], bool],
        timeout: float = RECOVERY_TIMEOUT_SECONDS,
        cooldown: float = RECOVERY_COOLDOWN_SECONDS,
        observe: Callable[[str, str, str], None] = lambda _stage, _code, _operation: None,
        reconcile: Callable[[], None] = lambda: None,
        blockers: Callable[[], list[dict[str, Any]]] | None = None,
        changed: Callable[[], object] = lambda: None,
        persist: Callable[[list[dict[str, Any]]], None] = lambda _: None,
        history: list[dict[str, Any]] | None = None,
        runtime_instance_id: str = "",
    ) -> None:
        self._ready, self._idle, self._repair = ready, idle, repair
        self._retryable = retryable
        self._timeout, self._cooldown = timeout, cooldown
        self._condition = threading.Condition(threading.RLock())
        self._publication_lock = threading.Lock()
        self._local = threading.local()
        self._flight: _Flight | None = None
        self._generation = 0
        self._active = 0
        self._last_finished = float("-inf")
        self._closed = False
        self._observe = observe
        self._reconcile = reconcile
        self._blockers = blockers
        self._changed = changed
        self._persist = persist
        self._runtime_instance_id = runtime_instance_id
        self._history: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, str] = {}
        self._errors: dict[str, BaseException] = {}
        for saved in (history or [])[-64:]:
            record = copy.deepcopy(saved)
            if record.get("result") is None:
                record.update(
                    result="interrupted",
                    phase="finished",
                    version=record["version"] + 1,
                    available_actions=["recheck", "export_diagnostics"],
                )
            self._history[record["recovery_id"]] = record
            for key in record.get("request_keys", []):
                self._requests[key] = record["recovery_id"]

    def _snapshot(self, flight: _Flight) -> dict[str, Any]:
        return {
            "runtime_instance_id": self._runtime_instance_id,
            "recovery_id": flight.operation,
            "phase": flight.phase,
            "result": flight.result,
            "version": flight.version,
            "created_at": flight.created_at,
            "updated_at": flight.updated_at,
            "deadline_at": flight.created_at + self._timeout,
            "cancel_requested": flight.cancelled,
            "blockers": copy.deepcopy(flight.blockers),
            "error_code": getattr(flight.error, "code", "recovery_" + flight.result)
            if flight.error is not None and flight.result is not None
            else None,
            "available_actions": ["cancel_recovery"]
            if flight.result is None
            else []
            if flight.result == "succeeded"
            else ["recheck", "export_diagnostics"],
            "request_keys": list(flight.request_keys),
        }

    def _publish(self, flight: _Flight) -> None:
        with self._publication_lock:
            with self._condition:
                flight.version += 1
                flight.updated_at = time.time()
                self._history[flight.operation] = self._snapshot(flight)
                while len(self._history) > 64:
                    oldest = next(iter(self._history))
                    removed = self._history.pop(oldest)
                    self._errors.pop(oldest, None)
                    for key in removed.get("request_keys", []):
                        self._requests.pop(key, None)
                records = copy.deepcopy(list(self._history.values()))
            self._persist(records)
        self._changed()

    def snapshot(self, recovery_id: str | None = None) -> dict[str, Any] | None:
        with self._condition:
            if recovery_id is None:
                recovery_id = (
                    self._flight.operation if self._flight else next(reversed(self._history), None)
                )
            value = self._history.get(recovery_id) if recovery_id else None
            result = copy.deepcopy(value) if value else None
        if result is not None:
            result.pop("request_keys", None)
        return result

    def failure(self, recovery_id: str) -> BaseException | None:
        with self._condition:
            return self._errors.get(recovery_id)

    def start(self, request_key: str, *, recheck: bool = False) -> dict[str, Any]:
        if (
            not request_key
            or len(request_key) > 128
            or not all(c.isalnum() or c in "._-" for c in request_key)
        ):
            raise ValueError("invalid recovery request key")
        with self._condition:
            if self._closed:
                raise RecoveryUnavailable("command runtime closed")
            known = self._requests.get(request_key)
            if known is not None:
                result = self.snapshot(known)
                assert result is not None
                return result
            latest = self.snapshot()
            if (
                not recheck
                and self._flight is None
                and latest is not None
                and latest["result"] == "blocked"
                and self._blockers is not None
                and latest["blockers"] == self._blockers()
            ):
                saved = self._history[latest["recovery_id"]]
                if len(saved["request_keys"]) >= 128:
                    raise RecoveryUnavailable("recovery request capacity exhausted")
                saved["request_keys"].append(request_key)
                self._persist(copy.deepcopy(list(self._history.values())))
                self._requests[request_key] = latest["recovery_id"]
                return latest
            flight = self._flight
            created = flight is None
            if flight is None:
                flight = _Flight(
                    retained=True,
                    stage="manual_recovery",
                    deadline=time.monotonic() + self._timeout,
                )
                self._flight = flight
            flight.retained = True
            if len(flight.request_keys) >= 128:
                raise RecoveryUnavailable("recovery request capacity exhausted")
            flight.request_keys.append(request_key)
            self._requests[request_key] = flight.operation
        try:
            self._publish(flight)  # Persist the retry key before any native work.
        except Exception:
            if created:
                with self._condition:
                    self._flight = None
                    flight.error = RecoveryUnavailable("recovery state could not be persisted")
                    flight.result = "failed"
                    flight.phase = "finished"
                    self._history[flight.operation] = self._snapshot(flight)
                    flight.done.set()
            raise
        if created:
            threading.Thread(
                target=self._work, args=(flight,), daemon=True, name="sandbox-recovery"
            ).start()
        result = self.snapshot(flight.operation)
        assert result is not None
        return result

    def cancel(self, recovery_id: str, expected_version: int) -> dict[str, Any]:
        with self._condition:
            flight = self._flight
            current = self.snapshot(recovery_id)
            if current is None:
                raise KeyError(recovery_id)
            if current["result"] is not None or current["cancel_requested"]:
                return current
            if (
                flight is None
                or flight.operation != recovery_id
                or current["version"] != expected_version
            ):
                raise RecoveryUnavailable("recovery version changed")
            flight.cancelled = True
            flight.phase = "stopping"
            self._condition.notify_all()
        self._publish(flight)
        return self.snapshot(recovery_id) or current

    def phase(self, phase: str) -> None:
        flight = getattr(self._local, "flight", None)
        if flight is not None:
            with self._condition:
                flight.phase = "stopping" if flight.cancelled else phase
            self._publish(flight)

    def _record(self, stage: str, code: str, operation: str) -> None:
        # Diagnostics must never change whether a business action is replayed.
        with suppress(Exception):
            self._observe(stage, code, operation)

    @contextmanager
    def scope(self, control: ToolInvocationControl, validate: Callable[[], None]) -> Iterator[None]:
        previous = getattr(self._local, "caller", None)
        self._local.caller = _Caller(control, validate)
        try:
            yield
        finally:
            self._local.caller = previous

    @contextmanager
    def bypass(self) -> Iterator[None]:
        previous = getattr(self._local, "bypass", False)
        self._local.bypass = True
        try:
            yield
        finally:
            self._local.bypass = previous

    def run(self, action: Callable[[], T]) -> T:
        if getattr(self._local, "bypass", False):
            return action()
        if getattr(self._local, "action_depth", 0):
            return action()
        caller = getattr(self._local, "caller", None) or _Caller(
            ToolInvocationControl(), lambda: None
        )
        while True:
            caller.control.check_cancelled()
            with self._condition:
                if self._closed:
                    raise RecoveryUnavailable("command runtime closed")
                generation = self._generation
                needs_recovery = self._flight is not None or not self._ready()
                if not needs_recovery:
                    self._active += 1
            if needs_recovery:
                self._recover(caller, generation)
                continue
            try:
                self._local.action_depth = 1
                caller.control.check_cancelled()
                caller.validate()
                result = action()
                if caller.operation:
                    self._record("recovery_resume", "ok", caller.operation)
                return result
            except Exception as exc:
                if caller.operation:
                    self._record("recovery_resume", "failed", caller.operation)
                if not self._retryable(exc):
                    raise
            finally:
                self._local.action_depth = 0
                with self._condition:
                    self._active -= 1
                    self._condition.notify_all()
            self._recover(caller, generation)

    def repair_explicit(self, *, timeout_seconds: float = 300.0) -> None:
        self._recover(
            _Caller(ToolInvocationControl(), lambda: None),
            self._generation,
            force=True,
            setup_timeout=timeout_seconds,
        )

    def setup_timeout_seconds(self, requested: float = 300.0) -> float:
        deadline = getattr(self._local, "deadline", float("inf"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecoveryUnavailable("automatic sandbox recovery timed out")
        return min(requested, getattr(self._local, "setup_timeout", requested), remaining)

    def checkpoint(self) -> None:
        check = getattr(self._local, "check", None)
        if check is not None:
            check()

    def _recover(
        self,
        caller: _Caller,
        generation: int,
        *,
        force: bool = False,
        setup_timeout: float = 300.0,
    ) -> None:
        caller.control.check_cancelled()
        if caller.used:
            raise RecoveryUnavailable("automatic sandbox recovery budget exhausted")
        caller.used = True
        caller.control.reserve_recovery_wait()
        with self._condition:
            # A concurrent failure from the old generation can reuse the repair
            # that just completed; it must not reprovision the same identities.
            if self._generation != generation and self._ready():
                return
            flight = self._flight
            if flight is None:
                if not force and time.monotonic() - self._last_finished < self._cooldown:
                    raise RecoveryUnavailable("automatic sandbox recovery is cooling down")
                flight = _Flight(
                    waiters=1,
                    setup_timeout=setup_timeout,
                    stage="manual_recovery" if force else "automatic_recovery",
                    deadline=time.monotonic() + self._timeout,
                )
                self._flight = flight
                threading.Thread(
                    target=self._work, args=(flight,), daemon=True, name="sandbox-recovery"
                ).start()
            else:
                flight.waiters += 1
            caller.operation = flight.operation
            self._condition.notify_all()
        deadline = flight.deadline
        try:
            while not flight.done.wait(0.05):
                caller.control.check_cancelled()
                if time.monotonic() >= deadline:
                    raise RecoveryUnavailable("automatic sandbox recovery wait timed out")
            caller.control.check_cancelled()
            if flight.error is not None:
                if isinstance(flight.error, RecoveryUnavailable):
                    raise RecoveryUnavailable(str(flight.error)) from flight.error
                raise RecoveryUnavailable("automatic sandbox recovery failed") from flight.error
            caller.validate()
        finally:
            with self._condition:
                flight.waiters -= 1
                self._condition.notify_all()

    def _work(self, flight: _Flight) -> None:
        deadline = flight.deadline
        self._local.deadline = deadline
        self._local.setup_timeout = flight.setup_timeout
        self._local.flight = flight

        def check() -> None:
            with self._condition:
                if (
                    self._closed
                    or flight.cancelled
                    or (flight.waiters == 0 and not flight.retained)
                ):
                    raise ToolInvocationCancelled("sandbox recovery has no active waiters")
            if time.monotonic() >= deadline:
                raise RecoveryUnavailable("automatic sandbox recovery timed out")

        self._local.check = check

        try:
            self._publish(flight)
            self._record(flight.stage, "requested", flight.operation)
            while True:
                check()
                with self._condition:
                    active = self._active
                # Reconciliation can publish evidence and must not hold the
                # coordinator lock. New actions cannot enter during this flight.
                if active == 0:
                    self._reconcile()
                if self._blockers is not None:
                    blocking = self._blockers()
                    if blocking or active:
                        raise RecoveryBlocked(
                            blocking
                            or [
                                {
                                    "reason": "active_preparation",
                                    "available_actions": ["wait_for_tasks"],
                                }
                            ]
                        )
                with self._condition:
                    if active == 0 and self._idle():
                        break
                    self._condition.wait(0.05)
            # Probes must not recursively join their own recovery flight.
            self.phase("repairing")
            with self.bypass():
                self._repair(check)
            check()
            if not self._ready():
                raise RecoveryUnavailable("sandbox recovery did not establish readiness")
            self._record(flight.stage, "ok", flight.operation)
            flight.result = "succeeded"
        except BaseException as exc:
            flight.error = exc
            self._errors[flight.operation] = exc
            if isinstance(exc, RecoveryBlocked):
                flight.blockers = exc.blockers
                flight.result = "blocked"
            elif isinstance(exc, OSError) and str(exc) in {
                "sandbox_maintenance_busy",
                "sandbox_maintenance_previous_owner_lost",
            }:
                flight.blockers = [
                    {
                        "operation_id": None,
                        "reason": str(exc),
                        "available_actions": ["recheck", "export_diagnostics"],
                    }
                ]
                flight.result = "blocked"
            elif isinstance(exc, ToolInvocationCancelled):
                flight.result = "interrupted" if self._closed else "cancelled"
            else:
                flight.result = "failed"
            if not flight.blockers and self._blockers is not None:
                flight.blockers = self._blockers()
            self._record(
                flight.stage,
                "cancelled" if isinstance(exc, ToolInvocationCancelled) else "failed",
                flight.operation,
            )
        finally:
            flight.phase = "finished"
            try:
                self._publish(flight)
            except Exception:
                flight.error = RecoveryUnavailable("recovery state could not be persisted")
                flight.result = "failed"
                with self._condition:
                    self._history[flight.operation] = self._snapshot(flight)
            with self._condition:
                self._generation += 1
                self._last_finished = time.monotonic()
                self._flight = None
                flight.done.set()
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            flight = self._flight
            self._condition.notify_all()
        if flight is not None:
            flight.done.wait(8)
