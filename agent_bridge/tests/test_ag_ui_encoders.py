"""AG-UI encoder tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, cast

from agent_bridge.protocols.ag_ui.encoders import encode_stream
from agent_bridge.session_turns import SessionTurnCoordinator

from agent import Conversation, TurnDone


@dataclass
class _SessionStub:
    session_id: str


class _ConversationStub:
    session = _SessionStub(session_id="session-a")

    def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[TurnDone]:
        yield TurnDone(stop_reason="end_turn", total_tool_calls=0)


def test_encode_stream_waits_for_session_turn_lock() -> None:
    coordinator = SessionTurnCoordinator()
    factory_called = Event()
    result: list[bytes] = []

    def factory() -> Conversation:
        factory_called.set()
        return cast(Conversation, _ConversationStub())

    with coordinator.hold("session-a"):

        def consume() -> None:
            result.extend(
                encode_stream(
                    factory,
                    "hello",
                    thread_id="session-a",
                    run_id="run-a",
                    accept="text/event-stream",
                    turn_coordinator=coordinator,
                )
            )

        thread = Thread(target=consume)
        thread.start()
        assert not factory_called.wait(timeout=0.05)

    thread.join(timeout=2)
    assert factory_called.is_set()
    assert b"RUN_STARTED" in b"".join(result)
    assert b"RUN_FINISHED" in b"".join(result)
