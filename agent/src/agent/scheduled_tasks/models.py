"""Scheduled Agent Task domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..scheduling import Schedule


class ScheduledTaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXHAUSTED = "exhausted"
    DELETED = "deleted"


class ScheduledOccurrenceState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    RUNNING = "running"
    TERMINAL = "terminal"


class ScheduledOccurrenceOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"


class ScheduledTriggerKind(StrEnum):
    SCHEDULED = "scheduled"
    CATCH_UP = "catch_up"
    MANUAL = "manual"


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    name: str
    instruction: str
    schedule: Schedule
    next_fire_at_utc: datetime | None
    status: ScheduledTaskStatus
    persona_id: str | None
    persona_name: str
    model_id: str
    skill_id: str | None
    origin_session_id: str | None
    fire_count: int
    revision: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class ScheduledTaskSnapshot:
    task_id: str
    task_revision: int
    name: str
    instruction: str
    schedule: Schedule
    persona_id: str | None
    persona_name: str
    model_id: str
    skill_id: str | None


@dataclass(frozen=True)
class ScheduledOccurrence:
    id: str
    task_id: str
    task_revision: int
    trigger_kind: ScheduledTriggerKind
    scheduled_at_utc: datetime
    task_snapshot: ScheduledTaskSnapshot
    state: ScheduledOccurrenceState
    outcome: ScheduledOccurrenceOutcome | None
    reason_code: str
    coalesced_count: int
    coalesced_from_utc: datetime | None
    claim_until: datetime | None
    session_id: str | None
    run_id: str | None
    skill_source: str | None
    skill_version: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class ScheduledOccurrenceClaim:
    occurrence: ScheduledOccurrence
    task: ScheduledTask


@dataclass(frozen=True)
class ScheduledTaskListQuery:
    statuses: tuple[ScheduledTaskStatus, ...] = (
        ScheduledTaskStatus.ACTIVE,
        ScheduledTaskStatus.PAUSED,
        ScheduledTaskStatus.EXHAUSTED,
    )
    limit: int = 100
    offset: int = 0


@dataclass
class ScheduledTaskError(Exception):
    kind: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
