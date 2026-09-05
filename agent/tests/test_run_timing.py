from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from agent.runtime import RunHandle, RunOutcome, RunRequest, UserEvent
from agent.runtime.models import RunStatus, RunTerminal


def test_terminal_clock_shared_with_persistence_and_cancel_does_not_finish() -> None:
    handle = RunHandle(
        RunRequest(event=UserEvent(session_id="s", user_input="hi")), primary_consumer=False
    )
    assert handle.snapshot().started_at is None
    handle.mark_running()
    start = handle.snapshot().started_at
    assert start is not None
    handle.cancel()
    assert handle.status is RunStatus.RUNNING
    assert handle.snapshot().finished_at is None
    persisted: list[RunTerminal] = []
    handle.set_terminal_callback(persisted.append)
    assert handle.finish_once(RunOutcome.CANCELLED)
    assert not handle.finish_once(RunOutcome.COMPLETED)
    assert len(persisted) == 1
    assert handle.snapshot().finished_at == persisted[0].finished_at
    assert handle.snapshot().started_at == start


def test_persistence_failure_never_publishes_success() -> None:
    handle = RunHandle(
        RunRequest(event=UserEvent(session_id="s", user_input="hi")), primary_consumer=False
    )
    handle.mark_running()

    def fail(terminal: RunTerminal) -> None:
        # A callback may read the handle without deadlocking or finalizing twice.
        assert handle.snapshot().finished_at is None
        assert not handle.finish_once(RunOutcome.COMPLETED)
        raise OSError("fixture disk failure")

    handle.set_terminal_callback(fail)
    assert handle.finish_once(RunOutcome.COMPLETED)
    assert handle.snapshot().outcome is RunOutcome.FAILED
    assert isinstance(handle.terminal.error, OSError) if handle.terminal is not None else False


def test_queued_run_termination_has_no_execution_start() -> None:
    handle = RunHandle(
        RunRequest(event=UserEvent(session_id="s", user_input="hi")), primary_consumer=False
    )
    handle.finish_once(RunOutcome.EXPIRED)
    assert handle.snapshot().started_at is None


@pytest.mark.parametrize("outcome", [RunOutcome.COMPLETED, RunOutcome.CANCELLED, RunOutcome.FAILED])
def test_clock_is_fixed_at_actual_terminal(
    monkeypatch: pytest.MonkeyPatch, outcome: RunOutcome
) -> None:
    import agent.runtime.models as models

    current = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)

    class Clock:
        @staticmethod
        def now(*args: Any) -> datetime:
            return current

    monkeypatch.setattr(models, "datetime", Clock)
    handle = RunHandle(
        RunRequest(event=UserEvent(session_id="s", user_input="hi")), primary_consumer=False
    )
    current = datetime(2026, 9, 5, 0, 1, tzinfo=UTC)
    handle.mark_running()
    current = datetime(2026, 9, 5, 0, 3, tzinfo=UTC)
    handle.finish_once(outcome)
    snapshot = handle.snapshot()
    assert snapshot.started_at is not None and snapshot.finished_at is not None
    assert (snapshot.finished_at - snapshot.started_at).total_seconds() == 120
    current = datetime(2026, 9, 6, tzinfo=UTC)
    assert handle.snapshot() == snapshot
