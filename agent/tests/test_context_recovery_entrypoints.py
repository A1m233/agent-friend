"""Recovery contracts through accepted inputs, real tool loops and cold restoration."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent.context import SummarizingContextManager
from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled
from agent.sessions import SessionRolloutRecorder, restore_rollout
from agent.sessions.model_history import SessionModelHistory

from agent import Conversation, ToolRegistry, ToolResult
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderAssistantTurn,
    ProviderToolCall,
)

from .test_context_summarizing import _make_conversation
from .test_long_session_recovery import RecoveringModel, setup_task


def test_first_send_keeps_accepted_user_identity_in_durable_model_history(tmp_path: Path) -> None:
    model = RecoveringModel(context_window=20_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=model, context_manager=SummarizingContextManager()
    )
    conv._rollout_recorder = SessionRolloutRecorder(store, session)
    list(conv.stream("Keep this accepted goal", user_event_id="accepted-goal", run_id="first"))

    saved = store.load(session.session_id)
    assert [event.uuid for event in saved.active_events if event.type == "user_message"] == [
        "accepted-goal"
    ]
    goals = [
        item
        for item in restore_rollout(saved).history.items
        if item.role == "user" and item.content == "Keep this accepted goal"
    ]
    assert len(goals) == 1
    assert goals[0].meta["source_user_event_uuid"] == "accepted-goal"


class _RecordingAction:
    name: ClassVar[str] = "record_action"
    description: ClassVar[str] = "Execute one numbered local fixture action and return its result."
    result_context_retention: ClassVar[str] = "pinned"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"index": {"type": "integer"}},
        "required": ["index"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.executed: list[int] = []

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        index = int(args["index"])
        self.executed.append(index)
        return ToolResult(text=f"fixture-result-{index}:" + "x" * 3000)


class _LongTaskModel(RecoveringModel):
    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        if (
            request.control.trace_context
            and request.control.trace_context.operation == "context_compaction"
        ):
            yield from super().stream(request)
            return
        self.received.append(copy.deepcopy(request.messages))
        index = len(self.received) - 1
        if index < 8:
            call_id = f"action-{index}"
            arguments = json.dumps({"index": index})
            yield LLMToolCallDelta(
                index=0,
                tool_call_id=call_id,
                tool_name="record_action",
                args_json_delta=arguments,
            )
            yield LLMTurnDone(
                stop_reason="tool_use",
                assistant_turn=ProviderAssistantTurn(
                    content=None,
                    tool_calls=(ProviderToolCall(0, call_id, "record_action", arguments),),
                ),
            )
            return
        yield LLMTextDelta(text="all actions complete")
        yield LLMTurnDone(
            stop_reason="end_turn",
            assistant_turn=ProviderAssistantTurn(content="all actions complete"),
        )


def test_one_user_tool_loop_compacts_and_finishes_without_reexecuting_results(
    tmp_path: Path,
) -> None:
    model = _LongTaskModel(context_window=20_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=model, context_manager=SummarizingContextManager()
    )
    action = _RecordingAction()
    conv._tool_registry = ToolRegistry([action])
    conv._rollout_recorder = SessionRolloutRecorder(store, session)

    assert (
        conv.send("Complete all numbered actions without repeating successful ones")
        == "all actions complete"
    )
    assert action.executed == list(range(8))
    saved = store.load(session.session_id)
    users = [event for event in saved.active_events if event.type == "user_message"]
    assert len(users) == 1
    checkpoint = saved.latest_compaction()
    assert checkpoint is not None
    assert checkpoint.payload["source_user_event_uuid"] == users[0].uuid
    assert len([event for event in saved.active_events if event.type == "tool_call_result"]) == 8
    assert model.complete_calls > 0
    for request in model.received:
        SessionModelHistory.validate_provider_tool_history(request)
    assert "fixture-result-0:" not in str(model.received[-1])
    assert "Complete all numbered actions" in str(model.received[-1])


@pytest.mark.parametrize("boundary", ["during_summary", "before_send"])
def test_cancellation_preserves_accepted_input_without_committing_checkpoint(
    tmp_path: Path, boundary: str
) -> None:
    token = CancellationToken()

    class CancellingModel(RecoveringModel):
        def summary_response(self, request: ModelRequest) -> ModelResponse:
            response = super().summary_response(request)
            if boundary == "during_summary":
                token.cancel(CancelReason.EXPLICIT_REQUEST)
                assert request.control.cancel_check is not None
                request.control.cancel_check()
            return response

    model = CancellingModel(context_window=20_000)
    conv, session, store = setup_task(tmp_path, model)
    original_results = [
        event.uuid for event in session.active_events if event.type == "tool_call_result"
    ]

    def cancel_check() -> None:
        # Inject cancellation once a completed summary has become a pending
        # checkpoint, before the subsequent provider request is accepted.
        if boundary == "before_send" and conv._pending_compaction is not None:
            token.cancel(CancelReason.EXPLICIT_REQUEST)
        token.raise_if_cancelled()

    with pytest.raises(RunCancelled):
        list(
            conv.stream(
                "New accepted instruction: only report what remains",
                user_event_id="new-goal",
                run_id="new",
                cancel_check=cancel_check,
            )
        )
    saved = store.load(session.session_id)
    assert saved.latest_compaction() is None
    assert (
        next(event for event in saved.active_events if event.uuid == "new-goal").payload["content"]
        == "New accepted instruction: only report what remains"
    )
    assert [
        event.uuid for event in saved.active_events if event.type == "tool_call_result"
    ] == original_results
    assert model.complete_calls > 0
    assert model.received == []
    assert not any(
        item.meta.get("synthetic") == "context_summary"
        for item in restore_rollout(saved).history.items
    )


def test_cold_conversation_continues_from_checkpoint_without_resurrecting_large_history(
    tmp_path: Path,
) -> None:
    conv, session, store = setup_task(tmp_path, RecoveringModel(context_window=20_000))
    list(
        conv.continue_after_tool_error(
            run_id="recovered",
            source_run_id="old",
            recovery_mode="compact_then_resume",
        )
    )
    saved = store.load(session.session_id)
    checkpoint = saved.latest_compaction()
    assert checkpoint is not None
    restored = restore_rollout(saved)
    cold_model = RecoveringModel(context_window=20_000)
    restarted = Conversation(
        session=saved,
        store=store,
        llm_client=cold_model,
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        session_model_history=restored.history,
        session_model_context=restored.model_context,
        cache_epoch=restored.cache_epoch,
        catalog=conv._catalog,
    )
    restarted._rollout_recorder = SessionRolloutRecorder(store, saved)

    assert restarted.send("Continue from the saved recovery") == "ok"
    assert cold_model.complete_calls == 0
    assert "verified-result-0:" not in str(cold_model.received[-1])
    assert "不要重做成功动作" in str(cold_model.received[-1])
    assert (
        sum(
            "这是压缩后的结构化摘要" in str(message.get("content"))
            for message in cold_model.received[-1]
        )
        == 1
    )
    latest = store.load(session.session_id).latest_compaction()
    assert latest is not None and latest.uuid == checkpoint.uuid


def test_new_message_can_resume_after_a_prior_context_failure(tmp_path: Path) -> None:
    model = RecoveringModel(context_window=20_000)
    conv, session, store = setup_task(tmp_path, model)
    assert conv.send("Changed goal: only summarize the remaining work") == "ok"
    saved = store.load(session.session_id)
    assert saved.latest_compaction() is not None
    assert len([event for event in saved.active_events if event.type == "user_message"]) == 2
    assert "Changed goal: only summarize the remaining work" in str(model.received[-1])
    assert "verified-result-0:" not in str(model.received[-1])


def test_edit_resend_replaces_failed_branch_and_accepts_corrected_goal(tmp_path: Path) -> None:
    model = RecoveringModel(context_window=20_000)
    conv, session, store = setup_task(tmp_path, model)
    list(
        conv.edit_resend_latest(
            "Corrected goal: report status only",
            expected_user_uuid="goal",
            run_id="edited",
        )
    )
    saved = store.load(session.session_id)
    active_users = [event for event in saved.active_events if event.type == "user_message"]
    assert len(active_users) == 1
    assert active_users[0].uuid != "goal"
    assert active_users[0].payload["content"] == "Corrected goal: report status only"
    assert "Corrected goal: report status only" in str(model.received[-1])
    assert "verified-result-0:" not in str(model.received[-1])
    assert not any(event.type == "tool_call_result" for event in saved.active_events)
    assert any(event.type == "tool_call_result" for event in saved.events)
