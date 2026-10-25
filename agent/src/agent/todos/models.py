"""Todo、Reminder 与到期 occurrence 的领域类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..scheduling import OnceSchedule as OnceSchedule
from ..scheduling import RecurringSchedule as RecurringSchedule


class TodoStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReminderKind(StrEnum):
    ONCE = "once"
    RECURRING = "recurring"


class ReminderStatus(StrEnum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    DISABLED = "disabled"


class OccurrenceState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    DELIVERY_UNKNOWN = "delivery_unknown"
    FAILED = "failed"


class AgentResponseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReminderNotificationMode(StrEnum):
    SMART = "smart"
    ALL = "all"


class ReminderProjectionKind(StrEnum):
    IM_FIXED = "im_fixed"
    IM_AGENT = "im_agent"
    PET_FIXED = "pet_fixed"


class ReminderDeliveryState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


ReminderSchedule = OnceSchedule | RecurringSchedule


@dataclass(frozen=True)
class Todo:
    id: str
    title: str
    description: str
    status: TodoStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    revision: int = 1
    origin_kind: str | None = None
    origin_id: str | None = None


@dataclass(frozen=True)
class Reminder:
    id: str
    todo_id: str
    kind: ReminderKind
    timezone: str
    schedule: ReminderSchedule
    next_fire_at_utc: datetime | None
    status: ReminderStatus
    fire_count: int
    revision: int
    created_at: datetime
    updated_at: datetime
    origin_session_id: str | None = None


@dataclass(frozen=True)
class TodoDetails:
    todo: Todo
    reminders: tuple[Reminder, ...] = ()
    last_operation: dict[str, Any] | None = None
    # Scheduled occurrence times, independent of notification delivery outcome.
    last_occurrences: dict[str, datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class ReminderFacts:
    occurrence_id: str
    todo_id: str
    reminder_id: str
    title: str
    description: str
    scheduled_at_utc: datetime
    scheduled_local: datetime
    timezone: str
    recurrence: str
    todo_status: TodoStatus


@dataclass(frozen=True)
class ReminderOccurrence:
    id: str
    reminder_id: str
    todo_id: str
    scheduled_at_utc: datetime
    facts: ReminderFacts
    state: OccurrenceState
    destination_kind: str | None
    destination_id: str | None
    delivery_receipt: str | None
    agent_response_status: AgentResponseStatus
    attempt_count: int
    next_attempt_at: datetime | None
    claim_until: datetime | None
    created_at: datetime
    updated_at: datetime
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    persistence_session_id: str | None = None
    persistence_destination_id: str | None = None
    persistence_reason: str | None = None
    notification_mode: ReminderNotificationMode | None = None
    notification_decision_kind: str | None = None


@dataclass(frozen=True)
class ReminderDelivery:
    occurrence_id: str
    projection_kind: ReminderProjectionKind
    destination_id: str | None
    state: ReminderDeliveryState
    receipt: str | None
    error_kind: str | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OccurrenceClaim:
    occurrence: ReminderOccurrence
    reminder: Reminder
    todo: Todo


@dataclass(frozen=True)
class TodoListQuery:
    statuses: tuple[TodoStatus, ...] = (TodoStatus.OPEN,)
    limit: int = 100
    offset: int = 0
    search: str = ""


@dataclass(frozen=True)
class TodoPatch:
    title: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class ReminderPatch:
    schedule: ReminderSchedule | None = None
    enabled: bool | None = None


@dataclass(frozen=True)
class DeliveryUpdate:
    state: OccurrenceState
    destination_kind: str | None = None
    destination_id: str | None = None
    delivery_receipt: str | None = None
    next_attempt_at: datetime | None = None
    agent_response_status: AgentResponseStatus | None = None


@dataclass
class TodoError(Exception):
    kind: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TodoError("invalid_datetime", "日期时间必须包含时区")
    return value.astimezone(UTC)
