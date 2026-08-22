"""Reminder driver for the shared schedule coordinator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from .models import DeliveryUpdate, OccurrenceState, ReminderOccurrence, TodoError
from .store import TodoStore

logger = logging.getLogger(__name__)

OccurrenceBatchHandler = Callable[[tuple[ReminderOccurrence, ...]], None]


class ReminderDriver:
    name: ClassVar[str] = "todo_reminder"

    def __init__(
        self,
        store: TodoStore,
        handler: OccurrenceBatchHandler,
        *,
        retry_delay: timedelta = timedelta(minutes=1),
    ) -> None:
        self._store = store
        self._handler = handler
        self._retry_delay = retry_delay

    def next_wakeup_at(self) -> datetime | None:
        return self._store.next_wakeup_at()

    def run_due(self, now: datetime) -> int:
        current = _as_utc(now)
        self._store.create_due_occurrences(current)
        claimed = self._store.claim_pending(current)
        if not claimed:
            return 0
        batch = tuple(claimed)
        try:
            self._handler(batch)
        except Exception:
            logger.exception(
                "Reminder occurrence batch delivery failed: %s",
                ",".join(occurrence.id for occurrence in batch),
            )
            for occurrence in batch:
                if not self._store.occurrence_is_deliverable(occurrence.id):
                    continue
                try:
                    self._store.update_occurrence(
                        occurrence.id,
                        DeliveryUpdate(
                            state=OccurrenceState.FAILED,
                            destination_kind=occurrence.destination_kind,
                            destination_id=occurrence.destination_id,
                            next_attempt_at=current + self._retry_delay,
                        ),
                        now=current,
                    )
                except TodoError:
                    logger.exception("Failed to persist occurrence failure: %s", occurrence.id)
        return len(claimed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TodoError("invalid_datetime", "ReminderDriver now 必须返回带时区时间")
    return value.astimezone(UTC)
