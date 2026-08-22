from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from agent.scheduled_tasks import (
    ScheduledOccurrenceOutcome,
    ScheduledOccurrenceState,
    ScheduledTaskError,
    ScheduledTaskListQuery,
    ScheduledTaskService,
    ScheduledTaskStatus,
    ScheduledTaskStore,
    ScheduledTriggerKind,
)
from agent.scheduling import OnceSchedule, RecurringSchedule


def _service(tmp_path: Path, now: datetime) -> tuple[ScheduledTaskStore, ScheduledTaskService]:
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    return store, ScheduledTaskService(store, clock=lambda: now)


def test_task_lifecycle_pause_resume_and_soft_delete(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    store, service = _service(tmp_path, now)
    task = service.create(
        name="日报",
        instruction="总结今天的工作",
        schedule=RecurringSchedule(
            frequency="daily",
            interval=1,
            local_time=time(9),
            timezone="UTC",
            starts_on=date(2026, 8, 2),
        ),
        persona_name="default",
        model_id="fake/model",
    )
    assert task.status is ScheduledTaskStatus.ACTIVE

    paused = service.pause(task.id, revision=task.revision)
    assert paused.status is ScheduledTaskStatus.PAUSED
    assert paused.next_fire_at_utc is None

    resumed = service.resume(paused.id, revision=paused.revision)
    assert resumed.status is ScheduledTaskStatus.ACTIVE
    assert resumed.next_fire_at_utc == datetime(2026, 8, 2, 9, tzinfo=UTC)

    deleted = service.delete(resumed.id, revision=resumed.revision)
    assert deleted.status is ScheduledTaskStatus.DELETED
    assert store.list_tasks(ScheduledTaskListQuery(statuses=(ScheduledTaskStatus.DELETED,))) == [
        deleted
    ]


def test_run_latest_coalesces_missed_calendar_occurrences(tmp_path: Path) -> None:
    created_at = datetime(2026, 8, 1, tzinfo=UTC)
    store, service = _service(tmp_path, created_at)
    task = service.create(
        name="日报",
        instruction="总结",
        schedule=RecurringSchedule(
            frequency="daily",
            interval=1,
            local_time=time(9),
            timezone="UTC",
            starts_on=date(2026, 8, 1),
        ),
        persona_name="default",
        model_id="fake/model",
    )
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)

    assert store.create_due_occurrences(now) == [store.list_occurrences(task.id)[0].id]
    occurrence = store.list_occurrences(task.id)[0]
    assert occurrence.scheduled_at_utc == datetime(2026, 8, 5, 9, tzinfo=UTC)
    assert occurrence.coalesced_count == 4
    assert occurrence.state is ScheduledOccurrenceState.PENDING
    assert occurrence.trigger_kind.value == "catch_up"


def test_missed_once_beyond_seven_days_is_expired(tmp_path: Path) -> None:
    created_at = datetime(2026, 8, 1, tzinfo=UTC)
    store, service = _service(tmp_path, created_at)
    task = service.create(
        name="一次",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )

    store.create_due_occurrences(datetime(2026, 8, 10, 10, tzinfo=UTC))
    occurrence = store.list_occurrences(task.id)[0]
    assert occurrence.state is ScheduledOccurrenceState.TERMINAL
    assert occurrence.outcome is ScheduledOccurrenceOutcome.EXPIRED


def test_small_dispatch_delay_is_not_reported_as_catch_up(tmp_path: Path) -> None:
    due_at = datetime(2026, 8, 2, 9, tzinfo=UTC)
    store, service = _service(tmp_path, datetime(2026, 8, 1, tzinfo=UTC))
    task = service.create(
        name="一次",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )

    store.create_due_occurrences(due_at + timedelta(seconds=30))
    occurrence = store.list_occurrences(task.id)[0]
    assert occurrence.trigger_kind is ScheduledTriggerKind.SCHEDULED


def test_updating_exhausted_task_with_future_schedule_reactivates_it(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    store, service = _service(tmp_path, now)
    task = service.create(
        name="一次",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 1, 1), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    store.create_due_occurrences(datetime(2026, 8, 1, 2, tzinfo=UTC))
    exhausted = store.get_task(task.id)
    assert exhausted.status is ScheduledTaskStatus.EXHAUSTED

    updated = service.update(
        task.id,
        revision=exhausted.revision,
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
    )
    assert updated.status is ScheduledTaskStatus.ACTIVE
    assert updated.next_fire_at_utc == datetime(2026, 8, 2, 9, tzinfo=UTC)
    assert store.get_task(task.id) == updated


def test_occurrence_is_idempotent_non_overlapping_and_freezes_task_snapshot(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    store, service = _service(tmp_path, now)
    task = service.create(
        name="日报",
        instruction="旧指令",
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    first = service.run_now(task.id)
    updated = service.update(task.id, revision=task.revision, instruction="新指令")
    second = service.run_now(task.id)

    assert first.task_snapshot.instruction == "旧指令"
    assert updated.instruction == "新指令"
    assert second.state is ScheduledOccurrenceState.TERMINAL
    assert second.outcome is ScheduledOccurrenceOutcome.SKIPPED
    assert second.reason_code == "task_run_in_progress"

    store.mark_terminal(
        first.id,
        outcome=ScheduledOccurrenceOutcome.CANCELLED,
        reason_code="test_cleanup",
        now=now,
        expected=(ScheduledOccurrenceState.PENDING,),
    )
    assert len(store.create_due_occurrences(datetime(2026, 8, 2, 10, tzinfo=UTC))) == 1
    assert store.create_due_occurrences(datetime(2026, 8, 2, 10, tzinfo=UTC)) == []


def test_delete_rejects_stale_revision_even_after_soft_delete(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    _store, service = _service(tmp_path, now)
    task = service.create(
        name="一次",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    service.delete(task.id, revision=task.revision)

    with pytest.raises(ScheduledTaskError) as caught:
        service.delete(task.id, revision=task.revision)
    assert caught.value.kind == "revision_conflict"
    assert caught.value.details["actual_revision"] == task.revision + 1


def test_update_rejects_empty_patch(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    _store, service = _service(tmp_path, now)
    task = service.create(
        name="一次",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )

    with pytest.raises(ScheduledTaskError) as caught:
        service.update(task.id, revision=task.revision)
    assert caught.value.kind == "invalid_arguments"


def test_claim_is_recoverable_before_submit_but_not_after(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    store, service = _service(tmp_path, now)
    task = service.create(
        name="立即运行",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 2, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    manual = service.run_now(task.id)
    claim = store.claim_pending(now)[0]
    assert claim.occurrence.id == manual.id
    assert claim.occurrence.session_id
    assert claim.occurrence.run_id

    recovered = store.recover_startup(now + timedelta(minutes=1))
    assert recovered == {"released_claims": 1, "interrupted_runs": 0}
    reclaimed = store.claim_pending(now + timedelta(minutes=1))[0]
    store.mark_submitted(
        reclaimed.occurrence.id,
        now=now + timedelta(minutes=1),
        skill_source=None,
        skill_version=None,
    )
    recovered = store.recover_startup(now + timedelta(minutes=2))
    assert recovered == {"released_claims": 0, "interrupted_runs": 1}
    terminal = store.get_occurrence(manual.id)
    assert terminal.outcome is ScheduledOccurrenceOutcome.INTERRUPTED
