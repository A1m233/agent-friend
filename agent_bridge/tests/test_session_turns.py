"""Session turn coordination tests."""

from __future__ import annotations

from threading import Event, Thread

import pytest
from agent.runtime import SessionBusyError
from agent_bridge.session_turns import SessionTurnCoordinator


def test_same_session_turns_are_serialized() -> None:
    coordinator = SessionTurnCoordinator()
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    events: list[str] = []

    def first() -> None:
        with coordinator.hold("session-a"):
            events.append("first-enter")
            first_entered.set()
            assert release_first.wait(timeout=2)
            events.append("first-exit")

    def second() -> None:
        assert first_entered.wait(timeout=2)
        with coordinator.hold("session-a"):
            events.append("second-enter")
            second_entered.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()

    assert first_entered.wait(timeout=2)
    assert not second_entered.wait(timeout=0.05)

    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert events == ["first-enter", "first-exit", "second-enter"]


def test_different_session_turns_can_overlap() -> None:
    coordinator = SessionTurnCoordinator()
    release_first = Event()
    second_entered = Event()

    def first() -> None:
        with coordinator.hold("session-a"):
            assert release_first.wait(timeout=2)

    def second() -> None:
        with coordinator.hold("session-b"):
            second_entered.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()

    assert second_entered.wait(timeout=2)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)


def test_try_hold_fails_immediately_for_busy_session() -> None:
    coordinator = SessionTurnCoordinator()
    with coordinator.hold("session-a"):
        with pytest.raises(SessionBusyError), coordinator.try_hold("session-a"):
            pass

        with coordinator.try_hold("session-b"):
            pass
