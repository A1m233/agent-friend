"""Wakeups retain measured history across projection upgrades and compaction."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from agent.context import SummarizingContextManager
from agent.context.errors import ContextCompactionUnavailableError
from agent.context.usage_anchor import RequestMeasurement, UsageAnchor
from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled
from agent.sessions import SessionRolloutRecorder, restore_rollout

from agent import Conversation, Event
from llm_providers import LLMUsage

from .test_context_summarizing import _make_conversation, _seed_turns
from .test_long_session_recovery import RecoveringModel


@pytest.mark.parametrize("tool_mode", ["normal", "disabled"])
@pytest.mark.parametrize("origin_kind", [None, "inner_world"])
def test_system_trigger_does_not_invalidate_its_own_compaction(
    tmp_path: Path, tool_mode: Literal["normal", "disabled"], origin_kind: str | None
) -> None:
    model = RecoveringModel(context_window=20000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=model,
        context_manager=SummarizingContextManager(),
        origin_kind=origin_kind,
    )
    _seed_turns(session, store, n=30, size=1500)
    conv._session_model_history.reconcile(session)
    conv._rollout_recorder = SessionRolloutRecorder(store, session)
    list(
        conv.dispatch_system_turn(
            source_kind="companionship",
            system_prompt_addendum="看看最近的变化",
            presentation="none",
            tool_mode=tool_mode,
            run_id="wake",
        )
    )
    assert model.complete_calls > 0
    assert len(model.received) == 1
    saved = store.load(session.session_id)
    assert len([event for event in saved.events if event.type == "system_trigger"]) == 1
    assert saved.latest_compaction() is not None
    assert restore_rollout(saved).history.items


@pytest.mark.parametrize("interruption", ["cancel", "source_change"])
def test_system_checkpoint_is_not_committed_after_interruption(
    tmp_path: Path, interruption: str
) -> None:
    model = RecoveringModel(context_window=20000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=model,
        context_manager=SummarizingContextManager(),
        origin_kind="inner_world",
    )
    _seed_turns(session, store, n=30, size=1500)
    conv._session_model_history.reconcile(session)
    conv._rollout_recorder = SessionRolloutRecorder(store, session)
    token = CancellationToken()
    interrupted = False

    def interrupt_after_summary() -> None:
        nonlocal interrupted
        if conv._pending_compaction is not None and not interrupted:
            interrupted = True
            if interruption == "cancel":
                token.cancel(CancelReason.EXPLICIT_REQUEST)
            else:
                event = Event(
                    type="user_message",
                    uuid="concurrent-input",
                    ts=datetime.now(UTC),
                    payload={"content": "source changed"},
                )
                store.append_event(session.session_id, event)
                session.append(event)
        token.raise_if_cancelled()

    expected = RunCancelled if interruption == "cancel" else ContextCompactionUnavailableError
    with pytest.raises(expected):
        list(
            conv.dispatch_system_turn(
                source_kind="companionship",
                system_prompt_addendum="看看最近的变化",
                presentation="none",
                tool_mode="normal",
                run_id="wake",
                cancel_check=interrupt_after_summary,
            )
        )
    saved = store.load(session.session_id)
    assert model.complete_calls > 0
    assert model.received == []
    assert saved.latest_compaction() is None
    assert len([event for event in saved.events if event.type == "system_trigger"]) == 1
    assert not any(
        item.meta.get("synthetic") == "context_summary"
        for item in restore_rollout(saved).history.items
    )


@pytest.mark.parametrize("change", ["none", "text", "opaque", "route", "unknown_version"])
def test_projection_upgrade_restores_measured_opaque_history_but_checks_new_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    model = RecoveringModel(context_window=400000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=model, context_manager=SummarizingContextManager()
    )
    rendered = [
        {
            "role": "assistant",
            "content": "已核对的历史",
            "reasoning_details": [
                {"type": "reasoning", "encrypted_content": "opaque-old"},
            ],
        }
    ]
    with monkeypatch.context() as old:
        old.setattr("agent.conversation.INPUT_PROJECTION_VERSION", "session-files-v2")
        anchor = UsageAnchor.from_usage(
            RequestMeasurement.capture(
                rendered,
                conv._usage_scope(None),
                compatibility_scope=conv._usage_scope(None, compatibility_only=True),
            ),
            LLMUsage(prompt_tokens=110753, input_token_accounting="inclusive_total"),
        )
    assert anchor is not None
    event = Event(
        type="assistant_message",
        uuid="measured",
        ts=datetime.now(UTC),
        payload={"content": "已核对的历史", "partial": False},
        meta={
            "context_usage": {
                "model": session.current_model,
                "prompt_tokens": 110753,
                "attachment_input_projection": "session-files-v2",
                "input_anchor": anchor.payload(),
            }
        },
    )
    store.append_event(session.session_id, event)
    restarted = Conversation(
        session=store.load(session.session_id),
        store=store,
        llm_client=model,
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        catalog=conv._catalog,
    )
    if change == "text":
        rendered[0]["content"] = "新增的助手文件说明" * 30
    elif change == "opaque":
        rendered.append(
            {
                "role": "assistant",
                "reasoning_details": [
                    {"type": "reasoning", "encrypted_content": "opaque-new"},
                ],
            }
        )
    elif change == "route":
        monkeypatch.setattr(restarted, "_active_provider_route_identity", lambda: "new-route")
    elif change == "unknown_version":
        monkeypatch.setattr("agent.conversation.INPUT_PROJECTION_VERSION", "session-files-v99")
    estimate = restarted._estimate_rendered_input(rendered, None)
    if change in {"route", "unknown_version"}:
        assert estimate.mode == "full_estimate"
        assert estimate.reason == "request_scope_changed"
        assert estimate.unknown_tokens == 400000
    elif change == "opaque":
        assert estimate.unknown_tokens == 400000
        assert estimate.tokens >= 400000
    else:
        assert estimate.mode.startswith("usage_anchor")
        assert estimate.unknown_tokens == 0
        assert estimate.anchor_tokens == 110753
        assert 110753 <= estimate.tokens < 115000
        if change == "text":
            assert estimate.delta_tokens > 0
