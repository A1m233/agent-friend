"""Same-task recovery through the real Conversation and durable rollout boundaries."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.context import SummarizingContextManager
from agent.context.errors import ContextCompactionUnavailableError
from agent.sessions import SessionRolloutRecorder, restore_rollout

from agent import Conversation, Event, JsonlSessionStore, Session
from llm_providers import (
    LanguageModelCapabilities,
    LLMNetworkError,
    LLMStreamEvent,
    LLMUsage,
    ModelRequest,
    ModelResponse,
)

from .test_context_summarizing import _FakeLLM, _make_conversation


class RecoveringModel(_FakeLLM):
    capabilities = LanguageModelCapabilities(
        input_usage_scope="request", context_checkpoint_mode="replace_complete_groups"
    )


def append(
    session: Session, store: JsonlSessionStore, kind: str, identity: str, **payload: object
) -> None:
    event = Event(
        type=kind,  # type: ignore[arg-type]
        uuid=identity,
        ts=datetime.now(UTC),
        payload=dict(payload),
        meta={"run_id": "old"},
    )
    store.append_event(session.session_id, event)
    session.append(event)


def seed_task(session: Session, store: JsonlSessionStore) -> None:
    append(session, store, "run_started", "started", run_id="old")
    append(
        session,
        store,
        "user_message",
        "goal",
        content="核对已完成结果，再处理剩余工作；不要重做成功动作",
    )
    for index in range(8):
        append(session, store, "assistant_message", f"assistant-{index}", content="", partial=False)
        append(
            session,
            store,
            "tool_call_request",
            f"request-{index}",
            tool_call_id=f"call-{index}",
            tool_name="saved_action",
            args={"index": index},
        )
        append(
            session,
            store,
            "tool_call_result",
            f"result-{index}",
            tool_call_id=f"call-{index}",
            tool_name="saved_action",
            content=f"verified-result-{index}:" + "x" * 3000,
            is_error=False,
            executed=True,
        )
    append(
        session,
        store,
        "run_error",
        "failure",
        code="context_compaction_unavailable",
        message="fixture",
        recoverable=True,
        run_id="old",
    )
    append(session, store, "run_terminal", "stopped", status="failed", run_id="old")


def setup_task(tmp_path: Path, model: _FakeLLM) -> tuple[Conversation, Session, JsonlSessionStore]:
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=model, context_manager=SummarizingContextManager()
    )
    seed_task(session, store)
    conv._rollout_recorder = SessionRolloutRecorder(store, session)
    return conv, session, store


def test_compact_then_resume_preserves_actions_goal_and_cold_checkpoint(tmp_path: Path) -> None:
    model = RecoveringModel(context_window=20000)
    conv, session, store = setup_task(tmp_path, model)
    original_results = [e.uuid for e in session.events if e.type == "tool_call_result"]
    list(
        conv.continue_after_tool_error(
            run_id="new", source_run_id="old", recovery_mode="compact_then_resume"
        )
    )
    checkpoint = session.latest_compaction()
    assert checkpoint is not None and checkpoint.payload["checkpoint_version"] == 4
    assert checkpoint.payload["source_user_event_uuid"] == "goal"
    assert 1 <= model.complete_calls <= 4
    assert [e.uuid for e in session.events if e.type == "user_message"] == ["goal"]
    assert [e.uuid for e in session.events if e.type == "tool_call_result"] == original_results
    assert any("不要重做成功动作" in str(m.get("content")) for m in model.received[-1])
    cold_session = store.load(session.session_id)
    cold_history = restore_rollout(cold_session).history
    anchors = [m for m in cold_history.items if m.meta.get("synthetic") == "task_anchor"]
    assert len(anchors) == 1 and anchors[0].uuid != "goal"
    from agent.context.checkpoints import resolve_prior_summary

    resolution = resolve_prior_summary(
        cold_session.active_events, cold_history.items, scope="model"
    )
    assert resolution.status == "materialized"
    assert len(cold_history.items) < len(cold_session.business_llm_messages)


def test_provider_rejection_does_not_commit_generated_checkpoint(tmp_path: Path) -> None:
    class Rejecting(RecoveringModel):
        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            if (
                request.control.trace_context
                and request.control.trace_context.operation == "context_compaction"
            ):
                yield from super().stream(request)
                return
            raise LLMNetworkError("offline")

    model = Rejecting(context_window=20000)
    conv, session, store = setup_task(tmp_path, model)
    with pytest.raises(LLMNetworkError):
        list(
            conv.continue_after_tool_error(
                run_id="retry", source_run_id="old", recovery_mode="compact_then_resume"
            )
        )
    assert model.complete_calls > 0
    assert store.load(session.session_id).latest_compaction() is None
    assert not any(
        m.meta.get("synthetic") == "task_anchor" for m in conv._session_model_history.items
    )


def test_source_change_during_summary_preserves_new_input_and_discards_candidate(
    tmp_path: Path,
) -> None:
    class Changed(RecoveringModel):
        def summary_response(self, request: ModelRequest) -> ModelResponse:
            response = super().summary_response(request)
            if self.complete_calls == 1:
                append(session, store, "user_message", "correction", content="新修正：先停止原任务")
            return response

    model = Changed(context_window=20000)
    conv, session, store = setup_task(tmp_path, model)
    with pytest.raises(ContextCompactionUnavailableError, match="会话已变化"):
        list(
            conv.continue_after_tool_error(
                run_id="retry", source_run_id="old", recovery_mode="compact_then_resume"
            )
        )
    saved = store.load(session.session_id)
    assert saved.latest_compaction() is None
    assert any(e.uuid == "correction" for e in saved.active_events)
    assert not model.received


def test_same_source_reuses_budget_but_real_input_starts_new_cycle(tmp_path: Path) -> None:
    conv, session, store = setup_task(tmp_path, RecoveringModel(context_window=20000))
    conv._session_model_history.reconcile(session)
    first = conv._build_runtime_context().compaction_control
    assert first is not None
    first.summary_requests = 3
    assert conv._build_runtime_context().compaction_control is first
    append(session, store, "user_message", "new-goal", content="只整理结果")
    updated = conv._build_runtime_context().compaction_control
    assert updated is not None and updated is not first and updated.summary_requests == 0
    assert updated.current_user is not None and updated.current_user.uuid == "new-goal"


def test_context_error_details_survive_session_reload(tmp_path: Path) -> None:
    conv, session, store = setup_task(tmp_path, RecoveringModel(context_window=20000))
    conv.append_run_error_event(
        code="context_compaction_unavailable",
        message="safe",
        recoverable=True,
        source="fixture",
        run_id="retry",
        reason="input_cost_unknown",
        recovery_action="compact_then_resume",
    )
    error = next(
        e for e in reversed(store.load(session.session_id).events) if e.type == "run_error"
    )
    assert error.payload["reason"] == "input_cost_unknown"
    assert error.payload["recovery_action"] == "compact_then_resume"


def test_recovery_replaces_old_opaque_group_in_the_final_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.context.usage_anchor import RequestMeasurement, UsageAnchor
    from llm_providers.reasoning import ProviderReasoningState

    from llm_providers import ModelRuntimeConfig

    model = RecoveringModel(context_window=20000)
    conv, session, _ = setup_task(tmp_path, model)
    monkeypatch.setattr(
        conv, "_active_model_config", lambda: ModelRuntimeConfig(session.current_model, "medium")
    )
    conv._session_model_history.reconcile(session)
    assistant = next(
        m for m in reversed(conv._session_model_history.items) if m.role == "assistant"
    )
    assistant.meta["provider_reasoning_state"] = ProviderReasoningState(
        provider_id="fixture",
        model_id=session.current_model,
        format="reasoning_details",
        replay_scope="conversation",
        payload=({"encrypted_content": "synthetic-opaque"},),
    ).to_event_payload()
    original = [conv._render_context_message(m) for m in conv._session_model_history.items]
    assert any(m.get("reasoning_details") for m in original)
    conv._usage_anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            original,
            conv._usage_scope(None),
            compatibility_scope=conv._usage_scope(None, compatibility_only=True),
        ),
        LLMUsage(prompt_tokens=15000, input_token_accounting="inclusive_total"),
    )
    assert conv._usage_anchor is not None
    list(
        conv.continue_after_tool_error(
            run_id="retry", source_run_id="old", recovery_mode="compact_then_resume"
        )
    )
    assert session.latest_compaction() is not None
    assert not any(m.get("reasoning_details") for m in model.received[-1])
    assert any("不要重做成功动作" in str(m.get("content")) for m in model.received[-1])
