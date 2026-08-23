from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from agent.todos import (
    AgentResponseStatus,
    DeliveryUpdate,
    OccurrenceState,
    OnceSchedule,
    RecurringSchedule,
    ReminderDeliveryState,
    ReminderNotificationMode,
    ReminderProjectionKind,
    TodoError,
    TodoListQuery,
    TodoService,
    TodoStatus,
    TodoStore,
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 7, 28, 1, 0, tzinfo=UTC))


@pytest.fixture
def service(tmp_path: Path, clock: MutableClock) -> TodoService:
    return TodoService(TodoStore(tmp_path / "todos.db"), clock=clock)


def test_lifecycle_persists_and_revision_conflict(
    service: TodoService, tmp_path: Path, clock: MutableClock
) -> None:
    created = service.create(title="  提交材料  ", description="准备附件")
    assert created.todo.title == "提交材料"
    assert service.list()[0].todo.id == created.todo.id

    updated = service.update(created.todo.id, revision=1, title="提交申请材料")
    assert updated.todo.revision == 2
    with pytest.raises(TodoError) as caught:
        service.update(created.todo.id, revision=1, description="旧写入")
    assert caught.value.kind == "revision_conflict"

    completed = service.complete(created.todo.id, revision=2)
    assert completed.todo.status is TodoStatus.COMPLETED
    reopened = service.reopen(created.todo.id, revision=3)
    assert reopened.todo.status is TodoStatus.OPEN

    reopened_store = TodoStore(tmp_path / "todos.db")
    assert reopened_store.get_todo(created.todo.id).todo.revision == 4
    assert service.list(TodoListQuery(statuses=(TodoStatus.COMPLETED,))) == []


def test_complete_disables_reminders_and_reopen_does_not_restore(
    service: TodoService,
) -> None:
    created = service.create(title="复查项目")
    reminder = service.add_reminder(
        created.todo.id,
        schedule=RecurringSchedule(
            frequency="daily",
            interval=1,
            local_time=time(9),
            timezone="UTC",
            starts_on=date(2026, 7, 29),
        ),
    )
    service.complete(created.todo.id, revision=1)
    reopened = service.reopen(created.todo.id, revision=2)
    assert reopened.reminders[0].id == reminder.id
    assert reopened.reminders[0].status.value == "disabled"
    assert reopened.reminders[0].next_fire_at_utc is None


def test_once_occurrence_is_created_claimed_and_delivered(
    service: TodoService, clock: MutableClock
) -> None:
    created = service.create(title="提交材料")
    reminder = service.add_reminder(
        created.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 5), "Asia/Shanghai"),
    )
    clock.value = datetime(2026, 7, 28, 1, 6, tzinfo=UTC)
    created_ids = service.store.create_due_occurrences(clock.value)
    assert len(created_ids) == 1
    assert service.store.create_due_occurrences(clock.value) == []
    assert service.store.get_reminder(reminder.id).status.value == "exhausted"

    claimed = service.store.claim_pending(clock.value)
    assert [item.id for item in claimed] == created_ids
    occurrence = service.store.update_occurrence(
        created_ids[0],
        DeliveryUpdate(
            state=OccurrenceState.DELIVERED,
            destination_kind="desktop",
            destination_id="session-1",
            delivery_receipt="event-1",
            agent_response_status=AgentResponseStatus.PENDING,
        ),
        now=clock.value,
    )
    assert occurrence.state is OccurrenceState.DELIVERED
    assert occurrence.facts.title == "提交材料"


def test_recurring_missed_fires_coalesce_to_latest(
    service: TodoService, clock: MutableClock
) -> None:
    created = service.create(title="每日检查")
    reminder = service.add_reminder(
        created.todo.id,
        schedule=RecurringSchedule(
            frequency="daily",
            interval=1,
            local_time=time(9),
            timezone="UTC",
            starts_on=date(2026, 7, 28),
        ),
    )
    clock.value = datetime(2026, 7, 31, 12, tzinfo=UTC)
    ids = service.store.create_due_occurrences(clock.value)
    assert len(ids) == 1
    occurrence = service.store.get_occurrence(ids[0])
    assert occurrence.scheduled_at_utc == datetime(2026, 7, 31, 9, tzinfo=UTC)
    updated = service.store.get_reminder(reminder.id)
    assert updated.next_fire_at_utc == datetime(2026, 8, 1, 9, tzinfo=UTC)
    assert updated.fire_count == 1


def test_expired_claim_is_recovered(service: TodoService, clock: MutableClock) -> None:
    created = service.create(title="恢复测试")
    service.add_reminder(
        created.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )
    clock.value += timedelta(minutes=2)
    service.store.create_due_occurrences(clock.value)
    first = service.store.claim_pending(clock.value, lease=timedelta(seconds=5))
    assert len(first) == 1
    assert service.store.claim_pending(clock.value) == []
    clock.value += timedelta(seconds=6)
    second = service.store.claim_pending(clock.value)
    assert [item.id for item in second] == [first[0].id]


def test_cancel_invalidates_already_claimed_occurrence(
    service: TodoService, clock: MutableClock
) -> None:
    todo = service.create(title="不再提醒")
    service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )
    clock.value += timedelta(minutes=2)
    service.store.create_due_occurrences(clock.value)
    claimed = service.store.claim_pending(clock.value)[0]

    service.cancel(todo.todo.id, revision=1)

    invalidated = service.store.get_occurrence(claimed.id)
    assert invalidated.invalidated_at == clock.value
    assert invalidated.invalidation_reason == "todo_cancelled"
    assert invalidated.agent_response_status is AgentResponseStatus.SKIPPED
    assert not service.store.occurrence_is_deliverable(claimed.id)


def test_reminder_update_invalidates_old_pending_occurrence(
    service: TodoService, clock: MutableClock
) -> None:
    todo = service.create(title="改时间")
    reminder = service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )
    clock.value += timedelta(minutes=2)
    occurrence_id = service.store.create_due_occurrences(clock.value)[0]

    service.update_reminder(
        todo.todo.id,
        reminder.id,
        revision=2,
        schedule=OnceSchedule(datetime(2026, 7, 28, 10, 0), "Asia/Shanghai"),
    )

    invalidated = service.store.get_occurrence(occurrence_id)
    assert invalidated.invalidation_reason == "reminder_updated"
    assert service.store.claim_pending(clock.value) == []


def test_occurrence_context_freezes_once_and_delivery_states_are_independent(
    service: TodoService,
    clock: MutableClock,
) -> None:
    todo = service.create(title="冻结测试")
    service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
        origin_session_id="origin",
    )
    clock.value += timedelta(minutes=2)
    occurrence_id = service.store.create_due_occurrences(clock.value)[0]
    service.store.claim_pending(clock.value)

    first = service.store.freeze_occurrence_context(
        occurrence_id,
        persistence_session_id="session-c",
        persistence_destination_id="qq|bind-c|c2c|chat-c|session-c",
        persistence_reason="latest_im_interaction",
        notification_mode=ReminderNotificationMode.ALL,
        notification_decision_kind="all",
        now=clock.value,
    )
    second = service.store.freeze_occurrence_context(
        occurrence_id,
        persistence_session_id="session-b",
        persistence_destination_id="qq|bind-b|c2c|chat-b|session-b",
        persistence_reason="latest_im_binding",
        notification_mode=ReminderNotificationMode.SMART,
        notification_decision_kind="im",
        now=clock.value + timedelta(seconds=1),
    )

    assert first.persistence_session_id == "session-c"
    assert second.persistence_session_id == "session-c"
    assert second.notification_mode is ReminderNotificationMode.ALL
    assert second.notification_decision_kind == "all"

    service.store.ensure_delivery(
        occurrence_id,
        ReminderProjectionKind.IM_FIXED,
        destination_id=first.persistence_destination_id,
        now=clock.value,
    )
    service.store.ensure_delivery(
        occurrence_id,
        ReminderProjectionKind.PET_FIXED,
        destination_id="pet",
        now=clock.value,
    )
    service.store.ensure_delivery(
        occurrence_id,
        ReminderProjectionKind.IM_AGENT,
        destination_id=first.persistence_destination_id,
        now=clock.value,
    )
    first_attempt, first_started = service.store.begin_delivery_attempt(
        occurrence_id,
        ReminderProjectionKind.IM_AGENT,
        now=clock.value,
    )
    second_attempt, second_started = service.store.begin_delivery_attempt(
        occurrence_id,
        ReminderProjectionKind.IM_AGENT,
        now=clock.value + timedelta(seconds=1),
    )
    service.store.update_delivery(
        occurrence_id,
        ReminderProjectionKind.IM_FIXED,
        state=ReminderDeliveryState.DELIVERED,
        receipt="message-1",
        now=clock.value,
    )
    service.store.update_delivery(
        occurrence_id,
        ReminderProjectionKind.PET_FIXED,
        state=ReminderDeliveryState.FAILED,
        error_kind="pet_unavailable",
        now=clock.value,
    )

    deliveries = {
        item.projection_kind: item for item in service.store.list_deliveries(occurrence_id)
    }
    assert deliveries[ReminderProjectionKind.IM_FIXED].state is ReminderDeliveryState.DELIVERED
    assert deliveries[ReminderProjectionKind.IM_FIXED].receipt == "message-1"
    assert deliveries[ReminderProjectionKind.PET_FIXED].state is ReminderDeliveryState.FAILED
    assert deliveries[ReminderProjectionKind.PET_FIXED].error_kind == "pet_unavailable"
    assert first_started
    assert not second_started
    assert first_attempt.state is ReminderDeliveryState.UNKNOWN
    assert second_attempt.state is ReminderDeliveryState.UNKNOWN
    assert second_attempt.error_kind == "send_in_progress"
    assert second_attempt.attempt_count == 1


def test_legacy_reminder_origin_is_backfilled_once_from_todo_origin(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    timestamp = "2026-07-28T01:00:00Z"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE todos (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_at TEXT, cancelled_at TEXT, revision INTEGER NOT NULL,
                origin_kind TEXT, origin_id TEXT
            );
            CREATE TABLE reminders (
                id TEXT PRIMARY KEY, todo_id TEXT NOT NULL, kind TEXT NOT NULL,
                timezone TEXT NOT NULL, schedule_json TEXT NOT NULL, next_fire_at_utc TEXT,
                status TEXT NOT NULL, fire_count INTEGER NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO todos VALUES (?, ?, '', 'open', ?, ?, NULL, NULL, 1, 'session', ?)",
            ("todo-1", "旧 Todo", timestamp, timestamp, "origin-session"),
        )
        conn.execute(
            "INSERT INTO reminders VALUES (?, ?, 'once', 'UTC', '{}', NULL, 'active', 0, 1, ?, ?)",
            ("reminder-1", "todo-1", timestamp, timestamp),
        )

    TodoStore(path)

    with sqlite3.connect(path) as conn:
        value = conn.execute(
            "SELECT origin_session_id FROM reminders WHERE id = 'reminder-1'"
        ).fetchone()
    assert value == ("origin-session",)
