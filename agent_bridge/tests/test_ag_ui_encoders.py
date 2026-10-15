"""AG-UI encoder tests."""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, cast

from agent.attachments import AttachmentRef
from agent.conversation_events import AssistantImage, AssistantImageStart, ToolCallResult
from agent.runtime import RunHandle, RunOutcome, RunRequest, UserEvent
from agent_bridge.protocols.ag_ui.encoders import encode_stream
from agent_bridge.session_turns import SessionTurnCoordinator

from agent import (
    AssistantMessageEnd,
    AssistantMessageMetadata,
    AssistantMessagePhase,
    AssistantMessageStart,
    AssistantPhaseStatus,
    ContextCompactionStatus,
    ContextCompactionUnavailableError,
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


def test_pure_generated_image_encodes_asset_and_terminal_without_fake_text() -> None:
    image = AttachmentRef("a" * 64, "generated.png", "image", "image/png", 80, "a" * 64)

    class ImageConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            yield AssistantImageStart("picture", 0)
            yield AssistantImage("picture", 0, (image,))
            yield TurnDone()

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, ImageConversation()), "image", thread_id="s", run_id="r"
        )
    ).decode()
    events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    images = [e["value"] for e in events if e.get("name") == "agent_friend.assistant_image"]
    assert [e["type"] for e in images] == ["assistant_image_start", "assistant_image"]
    assert images[1]["images"] == [image.to_dict()]
    assert events[-1]["type"] == "RUN_FINISHED"
    assert not any(e["type"].startswith("TEXT_MESSAGE") for e in events)


def test_tool_image_extension_preserves_asset_identity_and_excludes_errors() -> None:
    from dataclasses import asdict

    image = AttachmentRef("a" * 64, "screen.png", "image", "image/png", 80, "a" * 64)
    for failed in (False, True):
        result = ToolCallResult(
            tool_call_id="provider",
            event_tool_call_id="event",
            tool_name="view_image",
            text="image",
            images=(image,),
            is_error=failed,
        )
        assert asdict(result)["images"][0] == image.to_dict()

        class ImageConversation(_ConversationStub):
            image_result = result

            def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
                yield self.image_result
                yield TurnDone(stop_reason="end_turn")

        body = b"".join(
            encode_stream(
                lambda: cast(Conversation, ImageConversation()), "hi", thread_id="s", run_id="r"
            )
        ).decode()
        events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        extensions = [event for event in events if event.get("name") == "agent_friend.tool_images"]
        if failed:
            assert not extensions
        else:
            assert extensions[0]["value"] == {"toolCallId": "event", "images": [image.to_dict()]}
            text_result = next(event for event in events if event["type"] == "TOOL_CALL_RESULT")
            assert text_result["toolCallId"] == "event"


def test_refusal_and_citations_encoded_as_message_metadata() -> None:
    parts = [
        {
            "content_index": 0,
            "kind": "refusal",
            "start": 0,
            "end": 2,
            "annotations": [{"type": "file_citation", "filename": "reference.txt"}],
        }
    ]

    class ContentConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            yield TextDelta("No", message_id="answer")
            yield AssistantMessageMetadata("answer", parts)
            yield TurnDone(stop_reason="refusal")

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, ContentConversation()), "hi", thread_id="s", run_id="r"
        )
    ).decode()
    events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    metadata = next(e for e in events if e.get("name") == "agent_friend.assistant_message_metadata")
    assert metadata["value"] == {"messageId": "answer", "contentParts": parts}
    assert events[-1]["type"] == "RUN_FINISHED"
    assert not any(e["type"] == "RUN_ERROR" for e in events)


def test_run_error_serializes_input_acceptance_and_exact_identity() -> None:
    for accepted in (False, True):
        handle = RunHandle(
            RunRequest(
                event=UserEvent(session_id="session-input", user_input="input"),
                run_id="run-input",
                runtime_instance_id="bridge-input",
                input_accepted=False,
            )
        )
        if accepted:
            handle.mark_input_accepted()
        handle.finish_once(RunOutcome.FAILED, error=RuntimeError("test failure"))
        body = b"".join(
            encode_stream(
                lambda: cast(Conversation, _ConversationStub()),
                "input",
                thread_id="session-input",
                run_id="run-input",
                run_handle=handle,
            )
        ).decode()
        events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        assert events[0]["type"] == "RUN_STARTED"
        assert events[0]["inputAccepted"] is False
        assert events[-1]["type"] == "RUN_ERROR"
        assert events[-1]["inputAccepted"] is accepted
        assert events[-1]["threadId"] == "session-input"
        assert events[-1]["runId"] == "run-input"
        assert events[-1]["runtimeInstanceId"] == "bridge-input"


def test_item_identity_and_phase_survive_agui_without_provider_decisions() -> None:
    class PhaseConversation(_ConversationStub):
        def stream(self, _user_input: str, **kwargs: Any) -> Iterator[ConversationEvent]:
            assert kwargs["run_id"] == "run-a"
            yield AssistantPhaseStatus("run-a", "native")
            for identity, phase in (("item-a", "commentary"), ("item-b", "unknown")):
                yield AssistantMessageStart(identity, cast(Any, phase))
                yield TextDelta(identity, message_id=identity, phase=cast(Any, phase))
                yield AssistantMessageEnd(identity)
                yield AssistantMessagePhase(identity, "final_answer")
            yield TextDelta("resumed", message_id="item-c", phase="final_answer")
            yield TurnDone()

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, PhaseConversation()),
            "hi",
            thread_id="session-a",
            run_id="run-a",
            accept="text/event-stream",
        )
    )
    assert body.count(b'"type":"TEXT_MESSAGE_START"') == 3
    assert body.count(b'"type":"TEXT_MESSAGE_END"') == 3
    assert body.count(b'"type":"TEXT_MESSAGE_CONTENT"') == 3
    assert body.count(b'"name":"agent_friend.assistant_message_phase"') == 2
    assert b'"phase":"final_answer"' in body
    assert b'"messageId":"item-a"' in body and b'"messageId":"item-b"' in body
    assert b'"phase":"commentary"' in body and b'"phase":"unknown"' in body
    assert b'"name":"agent_friend.assistant_phase"' in body


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
    assert handle.finish_once(
        RunOutcome.COMPLETED,
        reason_code="budget_exhausted",
        total_tool_calls=1,
        tool_call_items=2,
        model_calls=3,
        model_continuations=2,
        budget_dimension="total_tool_calls",
    )
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
    assert b'"reasonCode":"budget_exhausted"' in body
    assert b'"toolCallItems":2' in body
    assert b'"executedToolCalls":1' in body
    assert b'"modelCalls":3' in body
    assert b'"budgetDimension":"total_tool_calls"' in body


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


def test_encode_stream_maps_context_compaction_without_summary_payload() -> None:
    class CompactionConversation(_ConversationStub):
        def stream(self, _user_input: str, **_kwargs: Any) -> Iterator[ConversationEvent]:
            yield ContextCompactionStatus(activity_id="activity-1", phase="started")
            yield ContextCompactionStatus(activity_id="activity-1", phase="completed")
            yield TurnDone(stop_reason="end_turn", total_tool_calls=0)

    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, CompactionConversation()),
            "hello",
            thread_id="session-a",
            run_id="run-a",
            accept="text/event-stream",
        )
    )

    assert b"agent_friend.context_compaction" in body
    assert b'"activityId":"activity-1"' in body
    assert b'"phase":"completed"' in body
    assert b"summary" not in body


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


def test_encode_stream_reports_compaction_outage_without_claiming_success() -> None:
    request = RunRequest(event=UserEvent(session_id="session-a", user_input="continue"))
    handle = RunHandle(request)
    assert handle.mark_running()
    assert handle.finish_once(
        RunOutcome.FAILED,
        reason_code="executor_error",
        error=ContextCompactionUnavailableError("private provider detail"),
    )
    body = b"".join(
        encode_stream(
            lambda: cast(Conversation, _ConversationStub()),
            "continue",
            thread_id="session-a",
            run_id=request.run_id,
            accept="text/event-stream",
            run_handle=handle,
        )
    )
    assert b"RUN_ERROR" in body
    assert b"context_compaction_unavailable" in body
    assert b'"recoveryAction":"compact_then_resume"' in body
    assert b'"reason":"unavailable"' in body
    assert "会话压缩暂时失败".encode() in body
    assert b"turn_input_too_large" not in body
    assert b"private provider detail" not in body
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
