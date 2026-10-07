from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from agent.runtime import SessionBusyError
from agent.runtime.cancellation import CancelReason, RunCancelled
from agent.runtime.inbox import SystemTriggerEvent, UserEvent
from agent.runtime.models import (
    ConsumerAbandonPolicy,
    InteractiveBusyPolicy,
    RunCancelResult,
    RunHandle,
    RunOutcome,
    RunPriority,
    RunRequest,
    RunSnapshot,
    RunStatus,
)
from agent.runtime.scheduler import RunScheduler, RuntimeDrainReport


def _user(session_id: str, text: str) -> RunRequest:
    return RunRequest(event=UserEvent(session_id=session_id, user_input=text))


def _system(
    session_id: str,
    *,
    priority: RunPriority = RunPriority.BACKGROUND,
    dedupe_key: str | None = None,
    expires_at: datetime | None = None,
) -> RunRequest:
    return RunRequest(
        event=SystemTriggerEvent(
            session_id=session_id,
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        ),
        priority=priority,
        dedupe_key=dedupe_key,
        expires_at=expires_at,
    )


def test_same_session_serial_different_sessions_concurrent() -> None:
    release = threading.Event()
    two_active = threading.Event()
    lock = threading.Lock()
    active_by_session: dict[str, int] = {}
    max_by_session: dict[str, int] = {}
    total_active = 0

    def execute(request: RunRequest, _handle: object) -> None:
        nonlocal total_active
        with lock:
            active_by_session[request.session_id] = active_by_session.get(request.session_id, 0) + 1
            max_by_session[request.session_id] = max(
                max_by_session.get(request.session_id, 0),
                active_by_session[request.session_id],
            )
            total_active += 1
            if total_active >= 2:
                two_active.set()
        assert release.wait(2)
        with lock:
            active_by_session[request.session_id] -= 1
            total_active -= 1

    scheduler = RunScheduler(execute, max_workers=2)
    scheduler.start()
    a1 = scheduler.submit(_user("a", "1"))
    a2 = scheduler.submit(_user("a", "2"))
    b1 = scheduler.submit(_user("b", "1"))
    assert two_active.wait(2)
    assert max_by_session == {"a": 1, "b": 1}
    release.set()
    assert a1.wait(2) is not None
    assert a2.wait(2) is not None
    assert b1.wait(2) is not None
    assert scheduler.stop()


def test_interactive_cancels_queued_and_active_background() -> None:
    background_started = threading.Event()
    release = threading.Event()

    def execute(_request: RunRequest, handle: object) -> None:
        from agent.runtime.models import RunHandle

        assert isinstance(handle, RunHandle)
        background_started.set()
        while not release.wait(0.01):
            handle.token.raise_if_cancelled()

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    active = scheduler.submit(_system("s"))
    assert background_started.wait(2)
    queued = scheduler.submit(_system("s", dedupe_key="idle"))
    user = scheduler.submit(_user("s", "hello"))
    active_terminal = active.wait(2)
    queued_terminal = queued.wait(2)
    assert active_terminal is not None
    assert active_terminal.outcome is RunOutcome.CANCELLED
    assert active.token.cancellation is not None
    assert active.token.cancellation.reason is CancelReason.SUPERSEDED_BY_USER
    assert queued_terminal is not None
    assert queued_terminal.outcome is RunOutcome.CANCELLED
    release.set()
    assert user.wait(2) is not None
    assert scheduler.stop()


def test_dedupe_keeps_latest_queued_run() -> None:
    blocker = threading.Event()

    def execute(request: RunRequest, _handle: object) -> None:
        if request.session_id == "busy":
            assert blocker.wait(2)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    busy = scheduler.submit(_user("busy", "hold"))
    first = scheduler.submit(_system("s", dedupe_key="idle"))
    second = scheduler.submit(_system("s", dedupe_key="idle"))
    first_terminal = first.wait(2)
    assert first_terminal is not None
    assert first_terminal.outcome is RunOutcome.SKIPPED
    assert first_terminal.reason_code == "deduplicated"
    blocker.set()
    assert busy.wait(2) is not None
    second_terminal = second.wait(2)
    assert second_terminal is not None
    assert second_terminal.outcome is RunOutcome.COMPLETED
    assert scheduler.stop()


def test_expiry_and_capacity_have_distinct_outcomes() -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    blocker = threading.Event()

    def execute(_request: RunRequest, _handle: object) -> None:
        assert blocker.wait(2)

    scheduler = RunScheduler(
        execute,
        max_workers=1,
        max_queued_runs=2,
        max_session_queue=2,
        now_factory=lambda: now,
    )
    scheduler.start()
    expired = scheduler.submit(_system("expired", expires_at=now - timedelta(seconds=1)))
    active = scheduler.submit(_user("active", "hold"))
    queued_1 = scheduler.submit(_user("q", "1"))
    queued_2 = scheduler.submit(_user("q", "2"))
    rejected = scheduler.submit(_user("q", "3"))
    skipped = scheduler.submit(_system("other"))
    assert expired.terminal is not None and expired.terminal.outcome is RunOutcome.EXPIRED
    assert rejected.terminal is not None and rejected.terminal.outcome is RunOutcome.REJECTED
    assert skipped.terminal is not None and skipped.terminal.outcome is RunOutcome.SKIPPED
    blocker.set()
    for handle in (active, queued_1, queued_2):
        assert handle.wait(2) is not None
    assert scheduler.stop()


def test_cancellation_token_first_reason_wins() -> None:
    request = _user("s", "x")
    from agent.runtime.models import RunHandle

    handle = RunHandle(request)
    assert handle.cancel(CancelReason.CLIENT_DISCONNECTED)
    assert not handle.cancel(CancelReason.EXPLICIT_REQUEST)
    try:
        handle.token.raise_if_cancelled()
    except RunCancelled as exc:
        assert exc.cancellation.reason is CancelReason.CLIENT_DISCONNECTED
    else:  # pragma: no cover
        raise AssertionError("expected RunCancelled")


def test_stop_reports_non_clean_until_uncancellable_worker_returns() -> None:
    started = threading.Event()
    release = threading.Event()

    def execute(_request: RunRequest, _handle: object) -> None:
        started.set()
        assert release.wait(2)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    handle = scheduler.submit(_user("s", "hold"))
    assert started.wait(2)
    assert not scheduler.stop(timeout=0.01)
    assert handle.token.is_cancelled
    release.set()
    terminal = handle.wait(2)
    assert terminal is not None
    assert terminal.outcome is RunOutcome.CANCELLED
    assert scheduler.stop(timeout=2)


def test_drain_rejects_new_work_and_cancels_queued_runs() -> None:
    started = threading.Event()
    release = threading.Event()

    def execute(_request: RunRequest, _handle: RunHandle) -> None:
        started.set()
        release.wait(2)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    active = scheduler.submit(_user("active", "first"))
    assert started.wait(1)
    queued = scheduler.submit(_user("active", "second"))

    result: list[RuntimeDrainReport] = []

    def drain() -> None:
        result.append(scheduler.drain(grace_timeout=1, cancel_timeout=1))

    thread = threading.Thread(target=drain)
    thread.start()
    queued_terminal = queued.wait(1)
    rejected = scheduler.submit(_user("new", "third"))
    release.set()
    thread.join(2)

    report = result[0]
    assert queued_terminal is not None
    assert queued_terminal.outcome is RunOutcome.CANCELLED
    assert queued_terminal.reason_code == "runtime_draining"
    assert rejected.wait(1).outcome is RunOutcome.REJECTED  # type: ignore[union-attr]
    assert report.queued_cancelled == 1
    assert report.running_at_start == 1
    assert report.completed_during_grace == 1
    assert not report.timed_out
    assert active.wait(1).outcome is RunOutcome.COMPLETED  # type: ignore[union-attr]
    assert scheduler.stop()


def test_drain_cooperatively_cancels_worker_after_grace_timeout() -> None:
    started = threading.Event()

    def execute(_request: RunRequest, handle: RunHandle) -> None:
        started.set()
        while True:
            handle.token.raise_if_cancelled()
            time.sleep(0.005)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    handle = scheduler.submit(_user("active", "first"))
    assert started.wait(1)

    report = scheduler.drain(grace_timeout=0, cancel_timeout=1)

    terminal = handle.wait(1)
    assert report.running_at_start == 1
    assert report.completed_during_grace == 0
    assert report.cancellation_requested == 1
    assert report.still_running == 0
    assert not report.timed_out
    assert terminal is not None
    assert terminal.outcome is RunOutcome.CANCELLED
    assert scheduler.stop()


def test_interactive_frees_same_session_background_capacity_before_admission() -> None:
    release = threading.Event()

    def execute(_request: RunRequest, _handle: object) -> None:
        assert release.wait(2)

    scheduler = RunScheduler(
        execute,
        max_workers=1,
        max_queued_runs=3,
        max_session_queue=2,
    )
    scheduler.start()
    active = scheduler.submit(_user("busy", "hold"))
    background_1 = scheduler.submit(_system("s", dedupe_key="idle-1"))
    background_2 = scheduler.submit(_system("s", dedupe_key="idle-2"))
    user = scheduler.submit(_user("s", "important"))
    assert user.terminal is None
    assert background_1.terminal is not None
    assert background_1.terminal.outcome is RunOutcome.CANCELLED
    assert background_2.terminal is not None
    assert background_2.terminal.outcome is RunOutcome.CANCELLED
    release.set()
    assert active.wait(2) is not None
    assert user.wait(2) is not None
    assert scheduler.stop()


def test_session_mutation_rejects_active_and_queued_work() -> None:
    started = threading.Event()
    release = threading.Event()

    def execute(_request: RunRequest, _handle: object) -> None:
        started.set()
        assert release.wait(2)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    active = scheduler.submit(_user("active", "hold"))
    assert started.wait(2)
    with pytest.raises(SessionBusyError), scheduler.try_reserve_session_mutation("active"):
        pass

    queued = scheduler.submit(_user("queued", "wait"))
    with pytest.raises(SessionBusyError), scheduler.try_reserve_session_mutation("queued"):
        pass

    release.set()
    assert active.wait(2) is not None
    assert queued.wait(2) is not None
    assert scheduler.stop()


def test_runs_submitted_during_session_mutation_start_after_release() -> None:
    started = threading.Event()

    def execute(_request: RunRequest, _handle: object) -> None:
        started.set()

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    with scheduler.try_reserve_session_mutation("s"):
        handle = scheduler.submit(_user("s", "after switch"))
        assert not started.wait(0.05)
        assert handle.terminal is None

    assert started.wait(2)
    assert handle.wait(2) is not None
    assert scheduler.stop()


def test_run_lifecycle_snapshots_are_versioned() -> None:
    release = threading.Event()
    snapshots: list[RunSnapshot] = []

    def execute(_request: RunRequest, _handle: RunHandle) -> None:
        assert release.wait(2)

    scheduler = RunScheduler(execute, max_workers=1, on_run_changed=snapshots.append)
    scheduler.start()
    handle = scheduler.submit(_user("s", "hello"))
    deadline = time.monotonic() + 2
    while handle.status is not RunStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.005)
    release.set()
    assert handle.wait(2) is not None
    assert [snapshot.status for snapshot in snapshots] == [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.TERMINAL,
    ]
    assert [snapshot.version for snapshot in snapshots] == sorted(
        snapshot.version for snapshot in snapshots
    )
    assert snapshots[-1].outcome is RunOutcome.COMPLETED
    assert scheduler.stop()


def test_desktop_interactive_rejects_same_session_interactive_work() -> None:
    release = threading.Event()
    started = threading.Event()

    def execute(_request: RunRequest, _handle: RunHandle) -> None:
        started.set()
        assert release.wait(2)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    active = scheduler.submit(_user("s", "first"))
    assert started.wait(2)
    rejected = scheduler.submit(
        RunRequest(
            event=UserEvent(session_id="s", user_input="second"),
            interactive_busy_policy=InteractiveBusyPolicy.REJECT_IF_INTERACTIVE_BUSY,
        )
    )
    assert rejected.terminal is not None
    assert rejected.terminal.outcome is RunOutcome.REJECTED
    assert rejected.terminal.reason_code == "session_busy"
    release.set()
    assert active.wait(2) is not None
    assert scheduler.stop()


def test_precise_cancel_validates_session_and_scope_and_finishes_queued() -> None:
    scheduler = RunScheduler(lambda _request, _handle: None, max_workers=1)
    scheduler.start()
    with scheduler.try_reserve_session_mutation("s"):
        handle = scheduler.submit(
            RunRequest(
                event=UserEvent(session_id="s", user_input="hello"),
                control_scope="desktop_text",
            )
        )
        mismatch, _ = scheduler.cancel_run(
            handle.run_id,
            expected_session_id="other",
            required_control_scope="desktop_text",
        )
        assert mismatch is RunCancelResult.SESSION_MISMATCH
        scope_mismatch, _ = scheduler.cancel_run(
            handle.run_id,
            expected_session_id="s",
            required_control_scope="voice",
        )
        assert scope_mismatch is RunCancelResult.SCOPE_MISMATCH
        accepted, snapshot = scheduler.cancel_run(
            handle.run_id,
            expected_session_id="s",
            required_control_scope="desktop_text",
        )
        assert accepted is RunCancelResult.ACCEPTED
        assert snapshot is not None and snapshot.status is RunStatus.TERMINAL
        assert snapshot.outcome is RunOutcome.CANCELLED
    assert scheduler.stop()


def test_detached_consumer_does_not_cancel_or_backpressure_run() -> None:
    request = RunRequest(
        event=UserEvent(session_id="s", user_input="hello"),
        consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
    )
    handle = RunHandle(request, event_queue_size=1)
    handle.emit("first")
    events = handle.events()
    assert next(events) == "first"
    handle.abandon_consumer()
    assert not handle.token.is_cancelled
    started = time.monotonic()
    for index in range(100):
        handle.emit(index)
    assert time.monotonic() - started < 0.5
    assert handle.finish_once(RunOutcome.COMPLETED)
    assert handle.terminal is not None
    assert handle.terminal.outcome is RunOutcome.COMPLETED


def test_terminal_runs_release_scheduler_tracking_indexes() -> None:
    scheduler = RunScheduler(lambda _request, _handle: None, max_workers=2)
    scheduler.start()
    handles = [scheduler.submit(_user(f"s-{index}", "hello")) for index in range(20)]
    assert all(handle.wait(2) is not None for handle in handles)

    assert scheduler.stop()
    assert scheduler._handles == {}
    assert scheduler._requests == {}
    assert scheduler._dedupe == {}
    assert scheduler._mailboxes == {}


def test_explicit_requests_are_fifo_and_not_superseded() -> None:
    from dataclasses import replace

    started = threading.Event()
    release = threading.Event()
    order = []

    def execute(request: RunRequest, handle: RunHandle) -> None:
        order.append(request.run_id)
        if len(order) == 1:
            started.set()
            assert release.wait(3)
            handle.token.raise_if_cancelled()

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    first = scheduler.submit(replace(_user("s", "scheduled"), replaceable=False))
    assert started.wait(2)
    second = scheduler.submit(replace(_user("s", "manual"), replaceable=False))
    third = scheduler.submit(
        replace(_user("s", "scheduled again"), replaceable=False, supersede_lower_priority=False)
    )
    try:
        assert not first.token.is_cancelled
        release.set()
        for handle in (first, second, third):
            terminal = handle.wait(3)
            assert terminal is not None and terminal.outcome is RunOutcome.COMPLETED
        assert order == [first.run_id, second.run_id, third.run_id]
    finally:
        release.set()
        scheduler.stop()


def test_session_mutation_cancels_waiters_but_refuses_active_run() -> None:
    release = threading.Event()
    started = threading.Event()

    def execute(request: RunRequest, _handle: RunHandle) -> None:
        if request.session_id == "blocker":
            started.set()
            assert release.wait(3)

    scheduler = RunScheduler(execute, max_workers=1)
    scheduler.start()
    blocker = scheduler.submit(_user("blocker", "hold"))
    assert started.wait(2)
    waiter = scheduler.submit(_user("s", "waiting"))
    try:
        with (
            pytest.raises(SessionBusyError),
            scheduler.try_reserve_session_mutation("blocker", cancel_queued=True),
        ):
            pass
        with (
            pytest.raises(ValueError, match="mutation failed"),
            scheduler.try_reserve_session_mutation("s", cancel_queued=True),
        ):
            raise ValueError("mutation failed")
        assert waiter.status is RunStatus.QUEUED
        with scheduler.try_reserve_session_mutation("s", cancel_queued=True):
            late_waiter = scheduler.submit(_user("s", "arrived during mutation"))
            assert waiter.status is RunStatus.QUEUED
            assert late_waiter.status is RunStatus.QUEUED
        for handle in (waiter, late_waiter):
            terminal = handle.wait(1)
            assert terminal is not None and terminal.outcome is RunOutcome.CANCELLED
        release.set()
        assert blocker.wait(2)
    finally:
        release.set()
        scheduler.stop()
