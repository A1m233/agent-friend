"""AG-UI encoder tests."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, cast

from agent_bridge.protocols.ag_ui.encoders import encode_stream
from agent_bridge.session_turns import SessionTurnCoordinator

from agent import Conversation, ConversationEvent, TextDelta, TurnDone


@dataclass
class _SessionStub:
    session_id: str


class _ConversationStub:
    session = _SessionStub(session_id="session-a")

    def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
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


def test_closing_encoded_stream_closes_conversation_and_releases_turn_lock() -> None:
    coordinator = SessionTurnCoordinator()
    conversation_closed = Event()

    class InterruptibleConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            try:
                yield TextDelta(text="partial")
                yield TextDelta(text="unreachable")
            finally:
                conversation_closed.set()

    stream = encode_stream(
        lambda: cast(Conversation, InterruptibleConversation()),
        "hello",
        thread_id="session-a",
        run_id="run-a",
        accept="text/event-stream",
        turn_coordinator=coordinator,
    )

    assert b"RUN_STARTED" in next(stream)
    assert b"TEXT_MESSAGE_START" in next(stream)
    assert b"partial" in next(stream)
    cast(Generator[bytes, None, None], stream).close()

    assert conversation_closed.is_set()
    with coordinator.hold("session-a"):
        pass
