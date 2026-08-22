"""One process-local clock loop coordinating multiple persistent schedule drivers."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from .models import ScheduleError
from .protocol import ScheduleDriver

logger = logging.getLogger(__name__)


class ScheduleCoordinator:
    def __init__(
        self,
        drivers: tuple[ScheduleDriver, ...],
        *,
        clock: Callable[[], datetime] | None = None,
        idle_poll_seconds: float = 30.0,
    ) -> None:
        if not drivers:
            raise ValueError("ScheduleCoordinator 至少需要一个 driver")
        names = [driver.name for driver in drivers]
        if len(set(names)) != len(names):
            raise ValueError("ScheduleCoordinator driver name 必须唯一")
        self._drivers = drivers
        self._clock = clock or (lambda: datetime.now(UTC))
        self._idle_poll_seconds = idle_poll_seconds
        self._condition = threading.Condition()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._inflight: set[str] = set()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            self._stopping = False
            self._executor = ThreadPoolExecutor(
                max_workers=len(self._drivers),
                thread_name_prefix="ScheduleDriver",
            )
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="ScheduleCoordinator",
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            thread = self._thread
            executor = self._executor
            if thread is None:
                return True
            self._stopping = True
            self._condition.notify_all()
        thread.join(timeout=timeout)
        with self._condition:
            remaining = deadline - time.monotonic()
            while self._inflight and remaining > 0:
                self._condition.wait(timeout=remaining)
                remaining = deadline - time.monotonic()
            stopped = not thread.is_alive() and not self._inflight
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if stopped:
            with self._condition:
                self._thread = None
                self._executor = None
        return stopped

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def run_once(self) -> int:
        now = self._now()
        processed = 0
        for driver in self._drivers:
            wake_at = driver.next_wakeup_at()
            if wake_at is not None and wake_at <= now:
                processed += driver.run_due(now)
        return processed

    def diagnostics(self) -> dict[str, object]:
        with self._condition:
            inflight = frozenset(self._inflight)
        drivers: list[dict[str, object]] = []
        for driver in self._drivers:
            wake_at = driver.next_wakeup_at()
            drivers.append(
                {
                    "name": driver.name,
                    "in_flight": driver.name in inflight,
                    "next_wakeup_at": wake_at.isoformat() if wake_at is not None else None,
                }
            )
        return {"running": self._thread is not None, "drivers": drivers}

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                executor = self._executor
            if executor is None:  # pragma: no cover - defensive lifecycle race
                return
            now = self._now()
            due: list[ScheduleDriver] = []
            next_times: list[datetime] = []
            with self._condition:
                for driver in self._drivers:
                    try:
                        wake_at = driver.next_wakeup_at()
                    except Exception:
                        logger.exception("schedule driver next wake failed: %s", driver.name)
                        continue
                    if wake_at is None:
                        continue
                    if wake_at <= now and driver.name not in self._inflight:
                        self._inflight.add(driver.name)
                        due.append(driver)
                    elif driver.name not in self._inflight:
                        next_times.append(wake_at)
                for driver in due:
                    executor.submit(self._run_driver, driver, now)
                if self._stopping:
                    return
                if due:
                    timeout = self._idle_poll_seconds
                elif next_times:
                    timeout = max(
                        0.0,
                        min(
                            self._idle_poll_seconds,
                            (min(next_times) - self._now()).total_seconds(),
                        ),
                    )
                else:
                    timeout = self._idle_poll_seconds
                self._condition.wait(timeout=timeout)

    def _run_driver(self, driver: ScheduleDriver, now: datetime) -> None:
        try:
            driver.run_due(now)
        except Exception:
            logger.exception("schedule driver iteration failed: %s", driver.name)
        finally:
            with self._condition:
                self._inflight.discard(driver.name)
                self._condition.notify_all()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ScheduleError("invalid_datetime", "ScheduleCoordinator clock 必须返回带时区时间")
        return value.astimezone(UTC)
