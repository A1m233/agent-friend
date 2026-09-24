"""One bounded recovery flight shared by callers of a command runtime.

The coordinator never replays an ambiguous action. The owner supplies readiness,
quiescence and a narrow rejection classifier; ordinary business failures pass through.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import TypeVar
from uuid import uuid4

from ..tool_runtime.control import ToolInvocationCancelled, ToolInvocationControl

RECOVERY_TIMEOUT_SECONDS = 480.0
RECOVERY_COOLDOWN_SECONDS = 60.0
T = TypeVar("T")


class RecoveryUnavailable(RuntimeError):
    pass


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
    ) -> None:
        self._ready, self._idle, self._repair = ready, idle, repair
        self._retryable = retryable
        self._timeout, self._cooldown = timeout, cooldown
        self._condition = threading.Condition(threading.RLock())
        self._local = threading.local()
        self._flight: _Flight | None = None
        self._generation = 0
        self._active = 0
        self._last_finished = float("-inf")
        self._closed = False
        self._observe = observe

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
                )
                self._flight = flight
                threading.Thread(
                    target=self._work, args=(flight,), daemon=True, name="sandbox-recovery"
                ).start()
            else:
                flight.waiters += 1
            caller.operation = flight.operation
            self._condition.notify_all()
        deadline = time.monotonic() + self._timeout
        try:
            while not flight.done.wait(0.05):
                caller.control.check_cancelled()
                if time.monotonic() >= deadline:
                    raise RecoveryUnavailable("automatic sandbox recovery wait timed out")
            caller.control.check_cancelled()
            if flight.error is not None:
                raise RecoveryUnavailable("automatic sandbox recovery failed") from flight.error
            caller.validate()
        finally:
            with self._condition:
                flight.waiters -= 1
                self._condition.notify_all()

    def _work(self, flight: _Flight) -> None:
        deadline = time.monotonic() + self._timeout
        self._local.deadline = deadline
        self._local.setup_timeout = flight.setup_timeout

        def check() -> None:
            with self._condition:
                if self._closed or flight.waiters == 0:
                    raise ToolInvocationCancelled("sandbox recovery has no active waiters")
            if time.monotonic() >= deadline:
                raise RecoveryUnavailable("automatic sandbox recovery timed out")

        try:
            self._record(flight.stage, "requested", flight.operation)
            while True:
                check()
                with self._condition:
                    if self._active == 0 and self._idle():
                        break
                    self._condition.wait(0.05)
            # Probes must not recursively join their own recovery flight.
            with self.bypass():
                self._repair(check)
            check()
            if not self._ready():
                raise RecoveryUnavailable("sandbox recovery did not establish readiness")
            self._record(flight.stage, "ok", flight.operation)
        except BaseException as exc:
            flight.error = exc
            self._record(
                flight.stage,
                "cancelled" if isinstance(exc, ToolInvocationCancelled) else "failed",
                flight.operation,
            )
        finally:
            with self._condition:
                self._generation += 1
                self._last_finished = time.monotonic()
                self._flight = None
                flight.done.set()
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
