from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from agent.todos import (
    AgentResponseStatus,
    DeliveryUpdate,
    OccurrenceState,
    OnceSchedule,
    RecurringSchedule,
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
