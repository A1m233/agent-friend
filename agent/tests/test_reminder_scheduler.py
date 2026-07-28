from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent.todos import OnceSchedule, ReminderOccurrence, ReminderScheduler, TodoService, TodoStore


def test_run_once_claims_due_occurrence(tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    todo = service.create(title="测试")
    service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )
    handled: list[str] = []
    scheduler = ReminderScheduler(
        store, lambda occurrence: handled.append(occurrence.id), clock=lambda: now
    )

    assert scheduler.run_once() == 1
    assert len(handled) == 1


def test_handler_failure_becomes_retryable_failed(tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    todo = service.create(title="测试")
    service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )

    def fail(_occurrence: ReminderOccurrence) -> None:
        raise RuntimeError("boom")

    scheduler = ReminderScheduler(store, fail, clock=lambda: now)
    assert scheduler.run_once() == 1
    occurrence = store.diagnostics()[0]
    assert occurrence.state.value == "failed"
    assert occurrence.next_attempt_at is not None
