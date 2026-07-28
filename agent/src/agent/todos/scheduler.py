"""可恢复 Reminder scheduler：条件等待、到期生成和 occurrence claim。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .models import DeliveryUpdate, OccurrenceState, ReminderOccurrence, TodoError
from .store import TodoStore

logger = logging.getLogger(__name__)

OccurrenceHandler = Callable[[ReminderOccurrence], None]


class ReminderScheduler:
    def __init__(
        self,
        store: TodoStore,
        handler: OccurrenceHandler,
        *,
        clock: Callable[[], datetime] | None = None,
        idle_poll_seconds: float = 30.0,
        retry_delay: timedelta = timedelta(minutes=1),
    ) -> None:
        self._store = store
        self._handler = handler
        self._clock = clock or (lambda: datetime.now(UTC))
        self._idle_poll_seconds = idle_poll_seconds
        self._retry_delay = retry_delay
        self._condition = threading.Condition()
        self._stopping = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="ReminderScheduler",
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        with self._condition:
            thread = self._thread
            if thread is None:
                return True
            self._stopping = True
            self._condition.notify_all()
        thread.join(timeout=timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._condition:
                self._thread = None
        return stopped

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def run_once(self) -> int:
        now = self._now()
        self._store.create_due_occurrences(now)
        claimed = self._store.claim_pending(now)
        for occurrence in claimed:
            try:
                self._handler(occurrence)
            except Exception:
                logger.exception("Reminder occurrence delivery failed: %s", occurrence.id)
                try:
                    self._store.update_occurrence(
                        occurrence.id,
                        DeliveryUpdate(
                            state=OccurrenceState.FAILED,
                            destination_kind=occurrence.destination_kind,
                            destination_id=occurrence.destination_id,
                            next_attempt_at=now + self._retry_delay,
                        ),
                        now=now,
                    )
                except TodoError:
                    logger.exception("Failed to persist occurrence failure: %s", occurrence.id)
        return len(claimed)

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
            try:
                processed = self.run_once()
            except Exception:
                logger.exception("Reminder scheduler iteration failed")
                processed = 0
            with self._condition:
                if self._stopping:
                    return
                if processed:
                    timeout = 0.0
                else:
                    wake_at = self._store.next_wakeup_at()
                    if wake_at is None:
                        timeout = self._idle_poll_seconds
                    else:
                        timeout = max(
                            0.0,
                            min(self._idle_poll_seconds, (wake_at - self._now()).total_seconds()),
                        )
                self._condition.wait(timeout=timeout)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise TodoError("invalid_datetime", "ReminderScheduler clock 必须返回带时区时间")
        return value.astimezone(UTC)
