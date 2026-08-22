"""Scheduled Task driver for the shared schedule coordinator."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from .models import (
    ScheduledOccurrenceClaim,
    ScheduledOccurrenceOutcome,
    ScheduledTaskError,
)
from .store import ScheduledTaskStore

OccurrenceHandler = Callable[[tuple[ScheduledOccurrenceClaim, ...]], None]


class ScheduledTaskDriver:
    name: ClassVar[str] = "scheduled_task"

    def __init__(
        self,
        store: ScheduledTaskStore,
        handler: OccurrenceHandler,
        *,
        max_lateness: timedelta = timedelta(days=7),
    ) -> None:
        self._store = store
        self._handler = handler
        self._max_lateness = max_lateness

    def next_wakeup_at(self) -> datetime | None:
        return self._store.next_wakeup_at()

    def run_due(self, now: datetime) -> int:
        current = _as_utc(now)
        self._store.create_due_occurrences(current, max_lateness=self._max_lateness)
        claimed = tuple(self._store.claim_pending(current))
        if not claimed:
            return 0
        try:
            self._handler(claimed)
        except Exception:
            for item in claimed:
                try:
                    self._store.mark_terminal(
                        item.occurrence.id,
                        outcome=item.occurrence.outcome or ScheduledOccurrenceOutcome.FAILED,
                        reason_code="occurrence_handler_failed",
                        now=current,
                    )
                except ScheduledTaskError:
                    continue
            raise
        return len(claimed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ScheduledTaskError("invalid_datetime", "ScheduledTaskDriver now 必须带时区")
    return value.astimezone(UTC)
