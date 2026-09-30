from __future__ import annotations

from contextlib import ExitStack

from agent.sessions.residency import SessionResidency

from agent import Event, Session


def test_unresolved_tool_transactions_remain_protected_until_paired() -> None:
    session = Session.new("t", "default", "m")
    for run in ("old", "new"):
        session.append(
            Event(
                type="tool_call_request",
                uuid=f"request-{run}",
                ts=session.created_at,
                payload={"tool_call_id": "same-provider-id", "event_tool_call_id": run},
                meta={"run_id": run},
            )
        )
    session.append(
        Event(
            type="tool_call_result",
            uuid="old-result",
            ts=session.created_at,
            payload={"tool_call_id": "same-provider-id", "event_tool_call_id": "old"},
            meta={"run_id": "old"},
        )
    )
    assert session.has_unresolved_tool_transactions()
    session.append(
        Event(
            type="tool_call_result",
            uuid="new-result",
            ts=session.created_at,
            payload={"tool_call_id": "same-provider-id"},
            meta={"run_id": "new"},
        )
    )
    assert not session.has_unresolved_tool_transactions()


def test_current_and_running_sessions_are_excluded_from_all_idle_limits() -> None:
    now = [0.0]
    policy = SessionResidency(max_bytes=1, max_entries=0, idle_seconds=1, clock=lambda: now[0])
    residents = {key: object() for key in ("current", "loading", "running", "idle")}
    released: list[str] = []

    def release(sid: str, expected: object) -> bool:
        assert residents[sid] is expected
        residents.pop(sid)
        released.append(sid)
        return True

    def sweep() -> None:
        policy.sweep(
            dict(residents),
            protected=lambda sid: sid == "running",
            size=lambda _: 1024,
            release=release,
        )

    with policy.using("current"), policy.using("loading"):
        sweep()
        now[0] = 1000
        sweep()
        assert released == ["idle"]
    sweep()
    assert set(released) == {"idle", "current", "loading"}
    assert "running" in residents


def test_multiple_holders_and_late_close_do_not_release_new_user() -> None:
    policy = SessionResidency(max_entries=0)
    state = object()
    released = []

    def release(sid: str, _: object) -> bool:
        released.append(sid)
        return True

    def sweep() -> None:
        policy.sweep(
            {"a": state},
            protected=lambda _: False,
            size=lambda _: 1,
            release=release,
        )

    with ExitStack() as old:
        old.enter_context(policy.using("a"))
        with policy.using("a"):
            old.close()
            sweep()
            assert not released
        sweep()
    assert released == ["a"]


def test_activity_between_sweeps_resets_deadline_and_size() -> None:
    now = [0.0]
    generation = [1]
    policy = SessionResidency(max_bytes=10, idle_seconds=10, clock=lambda: now[0])
    state = object()

    def sweep() -> tuple[str, ...]:
        return policy.sweep(
            {"a": state},
            protected=lambda _: False,
            size=lambda _: generation[0],
            release=lambda *_: True,
            revision=lambda _: generation[0],
        )

    assert not sweep()
    now[0], generation[0] = 9, 2
    assert not sweep()
    now[0] = 11
    assert not sweep()
    generation[0] = 20
    assert sweep() == ("a",)


def test_backend_race_cancels_eviction_and_restarts_idle_interval() -> None:
    now = [0.0]
    policy = SessionResidency(idle_seconds=10, clock=lambda: now[0])
    state = object()

    def sweep(release: bool) -> tuple[str, ...]:
        return policy.sweep(
            {"a": state}, protected=lambda _: False, size=lambda _: 1, release=lambda *_: release
        )

    assert not sweep(True)
    now[0] = 11
    assert not sweep(False)
    now[0] = 12
    assert not sweep(True)
    now[0] = 23
    assert sweep(True) == ("a",)
