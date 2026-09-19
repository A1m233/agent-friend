"""Shared user-turn and history-change contracts, observed at the model boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

import pytest
from agent.context import FifoContextManager, SummarizingContextManager
from agent.context.checkpoints import resolve_prior_summary
from agent.messages import Message
from agent.runtime.cancellation import Cancellation, CancelReason, RunCancelled
from agent.sessions.rollout import SessionRolloutRecorder, restore_rollout
from agent.skills.rendering import render_runtime_capability_notice
from agent.system_prompt import DESKTOP_CHAT_SURFACE, IM_QQ_SURFACE

from agent import ContextCompactionStatus, Event, ToolRegistry, ToolResult
from llm_providers import LLMEmptyResponseError

from .test_context_summarizing import (
    _FakeLLM,
    _make_conversation,
    _seed_large_tool_results,
    _seed_turns,
)


@pytest.mark.parametrize("outcome", ["reject", "cancel", "empty_twice", "recover"])
@pytest.mark.parametrize("entry", ["send", "edit"])
def test_candidate_checkpoints_follow_provider_acceptance(
    tmp_path: Path,
    outcome: str,
    entry: str,
) -> None:
    class Client(_FakeLLM):
        attempts = 0
        failing = False

        def stream(self, request: Any) -> Any:
            if self.failing:
                self.attempts += 1
                if outcome == "cancel":
                    raise RunCancelled(
                        Cancellation(CancelReason.EXPLICIT_REQUEST, datetime.now(UTC))
                    )
                if outcome == "reject":
                    raise RuntimeError("provider rejected request")
                if outcome == "empty_twice" or self.attempts == 1:
                    raise LLMEmptyResponseError("empty")
            yield from super().stream(request)

    llm = Client(context_window=128_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    _seed_turns(session, store, n=60, size=100)
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    conv.send("before")
    before_ids = [message.uuid for message in conv._session_model_history.items]
    llm.context_window = 14_000
    llm.failing = True
    events = []
    operation = conv.stream if entry == "send" else conv.edit_resend_latest
    try:
        for event in operation("new"):
            events.append(event)
    except (RuntimeError, LLMEmptyResponseError, RunCancelled):
        assert outcome != "recover"
    phases = [event.phase for event in events if isinstance(event, ContextCompactionStatus)]
    if outcome == "recover":
        assert phases == ["started", "completed"]
        assert session.latest_compaction() is not None
        restored = restore_rollout(store.load(session.session_id))
        assert (
            resolve_prior_summary(
                session.active_events, restored.history.snapshot(), scope="model"
            ).status
            == "materialized"
        )
        assert any(
            message.meta.get("synthetic") == "host_advisory_response_recovery"
            for message in restored.history.items
        )
    else:
        assert phases == ["started", "discarded"]
        assert session.latest_compaction() is None
        history = conv._session_model_history.items
        if entry == "send":
            assert [message.uuid for message in history][: len(before_ids)] == before_ids
        else:
            # Accepted edits replace the business branch even when the provider
            # rejects it; only the candidate summary remains uncommitted.
            assert [m.content for m in history if m.role == "user"][-1] == "new"
            assert not any(m.content == "before" for m in history)
            restored = restore_rollout(store.load(session.session_id))
            assert [m.uuid for m in restored.history.items] == [m.uuid for m in history]


def test_edit_does_not_reuse_checkpoint_created_in_replaced_turn(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=14_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    _seed_turns(session, store, n=60, size=100)
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    conv.send("replaced-question")
    old = session.latest_compaction()
    assert old is not None
    llm.summary_text = "<summary>new summary</summary>"
    list(conv.edit_resend_latest("replacement"))
    assert old.uuid not in {event.uuid for event in session.active_events}
    assert session.latest_compaction() is not None
    assert all("replaced-question" not in str(item.get("content")) for item in llm.received[-1])
    assert old.payload["summary"] not in str(llm.generate_requests[-1].messages)


def test_edit_commits_tool_projection_checkpoint(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=300_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
        with_recovery_tool=True,
    )
    refs = _seed_large_tool_results(session, store)
    event = Event(
        type="user_message", uuid=str(uuid4()), ts=datetime.now(UTC), payload={"content": "replace"}
    )
    session.append(event)
    store.append_event(session.session_id, event)
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    list(conv.edit_resend_latest("edited"))
    edit = session.latest_tool_context_edit()
    assert edit is not None
    assert edit.payload["newly_cleared_result_uuids"] == refs[:6]
    restored = restore_rollout(store.load(session.session_id))
    assert any("result_ref=" + refs[0] in item.content for item in restored.history.items)
    assert any(
        event.uuid == refs[0] and "large-0-" in str(event.payload) for event in session.events
    )


def _host_messages(conv: Any) -> dict[str, Message]:
    return {
        message.meta["model_context_contributor"]: message
        for message in conv._session_model_history.items
        if message.meta.get("model_context_contributor")
    }


@pytest.mark.parametrize("change", ["edit", "reconcile", "fifo"])
def test_current_environment_survives_history_changes(tmp_path: Path, change: str) -> None:
    llm = _FakeLLM(context_window=14_000 if change == "fifo" else 128_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=FifoContextManager() if change == "fifo" else SummarizingContextManager(),
    )
    list(conv.stream("first", surface=DESKTOP_CHAT_SURFACE))
    list(conv.stream("second", surface=DESKTOP_CHAT_SURFACE))
    before = _host_messages(conv)
    if change == "edit":
        list(conv.edit_resend_latest("edited", surface=DESKTOP_CHAT_SURFACE))
        assert all(message.get("content") != "second" for message in llm.received[-1])
    else:
        if change == "reconcile":
            event = Event(
                type="tool_context_edit",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"cleared_result_uuids": [], "policy_version": 2},
            )
            session.append(event)
            store.append_event(session.session_id, event)
        else:
            for i in range(60):
                conv._session_model_history.append(
                    [
                        Message(role="user", content=f"old {i}" + "x" * 300),
                        Message(role="assistant", content="answer" + "x" * 300),
                    ]
                )
        list(conv.stream("next", surface=DESKTOP_CHAT_SURFACE))
    after = _host_messages(conv)
    assert after.keys() == before.keys()
    for contributor, message in after.items():
        assert message.content.replace('kind="delta"', 'kind="snapshot"') == before[
            contributor
        ].content.replace('kind="delta"', 'kind="snapshot"')
        assert any(message.content == item.get("content") for item in llm.received[-1])
    list(conv.stream("unchanged", surface=DESKTOP_CHAT_SURFACE))
    assert {key: item.uuid for key, item in _host_messages(conv).items()} == {
        key: item.uuid for key, item in after.items()
    }


def test_edit_reuses_verified_summary_and_records_new_compaction(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=14_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    _seed_turns(session, store, n=60, size=100)
    conv._session_model_history.reconcile(session)
    history = []
    for message in conv._session_model_history.items:
        history.append(message)
        if message.role == "assistant":
            history.append(
                Message(role="system", content="stopped", meta={"synthetic": "turn_aborted"})
            )
    conv._session_model_history.items = history
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    conv.send("compress")
    conv.send("second")
    original = session.latest_compaction()
    assert original is not None
    assert original.payload["covered_through_uuid"] not in {
        event.uuid for event in session.active_events
    }
    list(conv.edit_resend_latest("edited", expected_user_content="second"))
    assert llm.complete_calls == 1
    assert conv._checkpoint_invalid_reason == ""
    list(conv.edit_resend_latest("edited again", expected_user_content="edited"))
    assert llm.complete_calls == 1
    assert all(message.get("content") not in {"second", "edited"} for message in llm.received[-1])
    restored = restore_rollout(store.load(session.session_id))
    assert (
        resolve_prior_summary(
            session.active_events, restored.history.snapshot(), scope="model"
        ).status
        == "materialized"
    )


def test_edit_compaction_reports_progress_and_commits_with_rollout(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=128_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    _seed_turns(session, store, n=60, size=100)
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    conv.send("first")
    llm.context_window = 14_000
    events = list(conv.edit_resend_latest("edited"))
    assert [event.phase for event in events if isinstance(event, ContextCompactionStatus)] == [
        "started",
        "completed",
    ]
    compaction = session.latest_compaction()
    assert compaction is not None
    accepted = next(
        event
        for event in reversed(session.events)
        if event.type == "rollout_commit" and event.payload["boundary"] == "provider_input_accepted"
    )
    assert compaction.uuid in accepted.payload["active_event_uuids"]
    assert accepted.payload["epoch_manifest"]["compaction_id"] == compaction.uuid
    prefix = session.events[: session.events.index(accepted) + 1]
    from agent.sessions import Session

    restored = restore_rollout(Session.from_events(prefix))
    assert resolve_prior_summary(prefix, restored.history.snapshot(), scope="model").status == (
        "materialized"
    )


@dataclass
class _Tool:
    name: str
    description: ClassVar[str] = "Fixture tool, never invoked."
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        raise AssertionError("No actual tool execution is allowed")


@pytest.mark.parametrize("surface", [DESKTOP_CHAT_SURFACE, IM_QQ_SURFACE])
@pytest.mark.parametrize("compress", [False, True])
def test_builder_and_skill_capabilities_agree_on_real_surface(
    tmp_path: Path,
    surface: Any,
    compress: bool,
) -> None:
    llm = _FakeLLM(context_window=14_000 if compress else 128_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    names = [
        "filesystem_search",
        "filesystem_read_text",
        "filesystem_create_directory",
        "filesystem_write_text",
        "filesystem_apply_patch",
        "request_filesystem_access",
    ]
    conv._tool_registry = ToolRegistry([cast(Any, _Tool(name)) for name in names])
    if compress:
        _seed_turns(session, store, n=80, size=200)
    for operation in (conv.stream, conv.edit_resend_latest):
        list(operation("question", surface=surface))
        content = _host_messages(conv)["runtime_capabilities"].content
        assert render_runtime_capability_notice(names, surface=surface) in content
        assert ("cannot grant a new local-file approval" in content) == (surface != "desktop_chat")
