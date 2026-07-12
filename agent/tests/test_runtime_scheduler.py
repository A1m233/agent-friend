from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from agent.runtime.cancellation import CancelReason, RunCancelled
from agent.runtime.inbox import SystemTriggerEvent, UserEvent
from agent.runtime.models import RunOutcome, RunPriority, RunRequest
from agent.runtime.scheduler import RunScheduler


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
