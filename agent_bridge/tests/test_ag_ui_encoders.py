"""AG-UI encoder tests."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, cast

from agent.runtime import RunHandle, RunOutcome, RunRequest, UserEvent
from agent_bridge.protocols.ag_ui.encoders import encode_stream
from agent_bridge.session_turns import SessionTurnCoordinator

from agent import (
    Conversation,
    ConversationEvent,
    MalformedToolCallError,
    ModelResponseStatus,
    SkillActivationStatus,
    TextDelta,
    TurnDone,
)
from llm_providers import LLMContentPolicyError


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


def test_encode_stream_consumes_run_handle_without_calling_conversation_factory() -> None:
    request = RunRequest(event=UserEvent(session_id="session-a", user_input="hello"))
    handle = RunHandle(request)
    assert handle.mark_running()
    handle.emit(TextDelta(text="from runtime"))
    handle.emit(TurnDone(stop_reason="end_turn", total_tool_calls=0))
    assert handle.finish_once(RunOutcome.COMPLETED)
    factory_called = False

    def factory() -> Conversation:
        nonlocal factory_called
        factory_called = True
        return cast(Conversation, _ConversationStub())

    body = b"".join(
        encode_stream(
            factory,
            "hello",
            thread_id="session-a",
            run_id=request.run_id,
            accept="text/event-stream",
            run_handle=handle,
        )
    )
    assert not factory_called
    assert b"from runtime" in body
    assert b"RUN_FINISHED" in body


def test_encode_stream_maps_model_response_status_to_custom_event() -> None:
    class StatusConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            yield ModelResponseStatus(
                phase="retrying",
                after_tool=True,
                attempt=2,
                max_attempts=2,
            )
            yield TurnDone(stop_reason="end_turn", total_tool_calls=0)

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, StatusConversation()),
            "hello",
            thread_id="session-a",
            run_id="run-a",
            accept="text/event-stream",
        )
    )

    assert b"CUSTOM" in body
    assert b"agent_friend.run_status" in body
    assert b'"afterTool":true' in body
    assert b'"attempt":2' in body


def test_encode_stream_maps_reasoning_activity_without_private_content() -> None:
    class StatusConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            yield ModelResponseStatus(phase="reasoning", after_tool=True)
            yield TurnDone(stop_reason="end_turn", total_tool_calls=0)

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, StatusConversation()),
            "hello",
            thread_id="session-a",
            run_id="run-a",
            accept="text/event-stream",
        )
    )

    assert b'"phase":"reasoning"' in body
    assert b"reasoning_content" not in body


def test_encode_stream_maps_skill_activation_to_custom_event() -> None:
    class SkillConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            yield SkillActivationStatus(
                status="activated",
                skill_id="skill_123",
                name="context7-mcp",
                source="personal",
                reason="由调用方为当前用户轮选择",
            )
            yield TurnDone(stop_reason="end_turn", total_tool_calls=0)

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, SkillConversation()),
            "hello",
            thread_id="session-a",
            run_id="run-a",
            accept="text/event-stream",
        )
    )

    assert b"agent_friend.skill_activation" in body
    assert b'"status":"activated"' in body
    assert b'"skillId":"skill_123"' in body


def test_encode_stream_maps_runtime_failure_from_terminal_error() -> None:
    request = RunRequest(event=UserEvent(session_id="session-a", user_input="hello"))
    handle = RunHandle(request)
    assert handle.mark_running()
    assert handle.finish_once(
        RunOutcome.FAILED,
        reason_code="executor_error",
        error=LLMContentPolicyError("Content Exists Risk"),
    )

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, _ConversationStub()),
            "hello",
            thread_id="session-a",
            run_id=request.run_id,
            accept="text/event-stream",
            run_handle=handle,
        )
    )

    assert b"RUN_ERROR" in body
    assert b"content_policy" in body
    assert "请求内容触发了后端模型的安全限制".encode() in body
    assert b"executor_error" not in body
    assert b"RUN_FINISHED" not in body


def test_encode_stream_exposes_malformed_tool_call_as_honest_run_error() -> None:
    request = RunRequest(event=UserEvent(session_id="session-a", user_input="hello"))
    handle = RunHandle(request)
    assert handle.mark_running()
    assert handle.finish_once(
        RunOutcome.FAILED,
        reason_code="malformed_tool_call",
        error=MalformedToolCallError("exec_command: missing reason"),
    )

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, _ConversationStub()),
            "hello",
            thread_id="session-a",
            run_id=request.run_id,
            accept="text/event-stream",
            run_handle=handle,
        )
    )

    assert b"RUN_ERROR" in body
    assert b"malformed_tool_call" in body
    assert "工具没有执行，也没有发起授权".encode() in body
    assert b"RUN_FINISHED" not in body


def test_encode_stream_maps_runtime_cancellation_to_finished_result() -> None:
    request = RunRequest(event=UserEvent(session_id="session-a", user_input="hello"))
    handle = RunHandle(request)
    assert handle.mark_running()
    handle.emit(TextDelta(text="partial"))
    assert handle.finish_once(
        RunOutcome.CANCELLED,
        reason_code="explicit_request",
    )

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, _ConversationStub()),
            "hello",
            thread_id="session-a",
            run_id=request.run_id,
            accept="text/event-stream",
            run_handle=handle,
        )
    )

    assert b"partial" in body
    assert b"RUN_FINISHED" in body
    assert b'"outcome":"cancelled"' in body
    assert b'"reasonCode":"explicit_request"' in body
    assert b"RUN_ERROR" not in body


def test_closing_run_handle_stream_cancels_abandoned_run() -> None:
    from agent.runtime.cancellation import CancelReason

    request = RunRequest(event=UserEvent(session_id="session-a", user_input="hello"))
    handle = RunHandle(request)
    assert handle.mark_running()
    handle.emit(TextDelta(text="partial"))
    stream = encode_stream(
        lambda: cast(Conversation, _ConversationStub()),
        "hello",
        thread_id="session-a",
        run_id=request.run_id,
        accept="text/event-stream",
        run_handle=handle,
    )
    assert b"RUN_STARTED" in next(stream)
    assert b"TEXT_MESSAGE_START" in next(stream)
    assert b"partial" in next(stream)
    cast(Generator[bytes, None, None], stream).close()
    assert handle.token.cancellation is not None
    assert handle.token.cancellation.reason is CancelReason.CONSUMER_ABANDONED
