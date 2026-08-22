from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent.todos import OnceSchedule, ReminderDriver, ReminderOccurrence, TodoService, TodoStore


def test_run_once_claims_due_occurrence(tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    todo = service.create(title="测试")
    service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )
    handled: list[tuple[str, ...]] = []
    driver = ReminderDriver(
        store,
        lambda occurrences: handled.append(tuple(item.id for item in occurrences)),
    )

    assert driver.run_due(now) == 1
    assert len(handled) == 1
    assert len(handled[0]) == 1


def test_run_once_delivers_one_claim_batch(tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    for title in ("测试一", "测试二"):
        todo = service.create(title=title)
        service.add_reminder(
            todo.todo.id,
            schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
        )
    handled: list[tuple[ReminderOccurrence, ...]] = []
    driver = ReminderDriver(store, handled.append)

    assert driver.run_due(now) == 2
    assert len(handled) == 1
    assert len(handled[0]) == 2


def test_handler_failure_becomes_retryable_failed(tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    for title in ("测试一", "测试二"):
        todo = service.create(title=title)
        service.add_reminder(
            todo.todo.id,
            schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
        )

    def fail(_occurrences: tuple[ReminderOccurrence, ...]) -> None:
        raise RuntimeError("boom")

    driver = ReminderDriver(store, fail)
    assert driver.run_due(now) == 2
    occurrences = store.diagnostics()
    assert len(occurrences) == 2
    assert all(occurrence.state.value == "failed" for occurrence in occurrences)
    assert all(occurrence.next_attempt_at is not None for occurrence in occurrences)
