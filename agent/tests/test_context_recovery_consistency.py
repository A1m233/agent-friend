"""Regressions for configuration changes and failures between durable boundaries."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent.context import SummarizingContextManager
from agent.context.errors import ContextCompactionUnavailableError
from agent.context.input_identity import input_compatibility_scope
from agent.context.usage_anchor import estimate_full_input
from agent.messages import Message
from agent.sessions import Event, SessionRolloutRecorder, restore_rollout
from agent.sessions.interruption import scope_interruption

from .test_context_summarizing import _FakeLLM, _make_conversation, _seed_turns
from .test_context_usage_continuity import anchor_for, estimate


def test_input_identity_ignores_output_controls_but_preserves_route_and_replay() -> None:
    base = {"route": "cpa/luna", "reasoning": "low", "defaults": {"reasoning_effort": "low"}}
    changed = {**base, "reasoning": "medium", "defaults": {"reasoning_effort": "medium"}}
    assert input_compatibility_scope(base) == input_compatibility_scope(changed)
    for field in ("route", "replay_scope", "input_usage_scope", "attachment_profile"):
        assert input_compatibility_scope(base) != input_compatibility_scope({**base, field: "new"})
    assert input_compatibility_scope(base) != input_compatibility_scope(
        {**base, "defaults": {"unknown_input_option": True}}
    )


def test_unknown_reasoning_has_one_admission_reserve_and_explicit_unknown_count() -> None:
    plain = {"role": "user", "content": "hi"}
    opaque = {"role": "assistant", "reasoning_details": [{"encrypted_content": "opaque"}]}
    anchor = anchor_for([plain], tokens=1000)
    result = estimate([plain, *([opaque] * 20)], anchor)
    assert result.tokens == 273000
    assert result.unknown_components == 20
    assert result.notes()["input_cost_known"] is False
    assert estimate_full_input([opaque] * 20, None, unknown_token_reserve=400000) == 400000


class _UnavailableContext:
    def build_messages(self, *args: Any, **kwargs: Any) -> Any:
        raise ContextCompactionUnavailableError("offline")


def test_failure_projection_is_idempotent_per_run_not_per_message(tmp_path: Path) -> None:
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=_FakeLLM(), context_manager=SummarizingContextManager()
    )
    for run_id in ("first", "first", "second"):
        conv.append_run_error_event(
            code="offline", message="failure", recoverable=True, source="fixture", run_id=run_id
        )
    errors = [e for e in store.load(session.session_id).active_events if e.type == "run_error"]
    assert [e.payload["run_id"] for e in errors] == ["first", "second"]


def test_preflight_removes_orphan_legacy_marker_without_restarting_old_task(tmp_path: Path) -> None:
    conv, session, _ = _make_conversation(
        tmp_path, fake_llm=_FakeLLM(), context_manager=SummarizingContextManager()
    )
    conv._session_model_history.append(
        [
            Message(role="user", content="current task"),
            Message(
                role="system",
                content="上一轮已中断",
                meta={"synthetic": "turn_aborted", "run_id": "inactive"},
            ),
        ]
    )
    events_before = list(session.events)
    conv._repair_incomplete_model_tool_transactions()
    assert [m.content for m in conv._session_model_history.items] == ["current task"]
    conv._repair_incomplete_model_tool_transactions()
    assert session.events == events_before


@pytest.mark.parametrize("legacy", [False, True])
def test_failed_edit_preserves_compacted_prefix_warm_and_cold(
    tmp_path: Path, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=_FakeLLM(context_window=20000),
        context_manager=SummarizingContextManager(),
    )
    conv._rollout_recorder = SessionRolloutRecorder(store, session)
    _seed_turns(session, store, n=20, size=600)
    conv.send("earlier compacted task")
    conv.send("last original task")
    assert len(conv._session_model_history.items) < len(session.business_llm_messages)
    before = len(conv._session_model_history.items)
    conv._context_manager = _UnavailableContext()
    if legacy:
        # Reproduce an old-version failure: only the business rewrite was committed.
        monkeypatch.setattr(conv, "_commit_accepted_user_input", lambda *_: None)
    with pytest.raises(ContextCompactionUnavailableError):
        list(conv.edit_resend_latest("replacement task", run_id="edit"))
    conv._session_model_history.reconcile(session)
    cold_session = store.load(session.session_id)
    cold = restore_rollout(cold_session).history
    cold.reconcile(cold_session)
    for history in (conv._session_model_history, cold):
        assert len(history.items) <= before
        users = [m.content for m in history.items if m.role == "user"]
        assert users[-1] == "replacement task"
        assert "last original task" not in users
        assert any("结构化摘要" in m.content for m in history.items)


@pytest.mark.parametrize("executed", [False, "unknown", True])
def test_old_interruption_moves_to_source_turn_and_does_not_replace_latest_request(
    executed: Any,
) -> None:
    ts = datetime(2026, 9, 14, tzinfo=UTC)
    request = Event(
        type="tool_call_request", uuid="call", ts=ts, payload={}, meta={"run_id": "old"}
    )
    result = Event(type="tool_call_result", uuid="result", ts=ts, payload={"executed": executed})
    items = [
        Message(role="user", content="old task"),
        Message(role="assistant", content="", uuid="call"),
        Message(role="tool", content="result", uuid="result"),
        Message(role="user", content="latest daily request"),
        Message(
            role="system",
            content="上一轮已中断",
            meta={"synthetic": "turn_aborted", "run_id": "old"},
        ),
    ]
    kwargs = dict(
        run_id="old", reason_code="explicit_request", result_origin="history_preflight_repair"
    )
    scoped = scope_interruption(items, [request], [result], **kwargs)
    assert scoped[-1].content == "latest daily request"
    marker = scoped[-2]
    assert "old" in marker.content and "2026-09-14" in marker.content
    assert ("执行结果未知" in marker.content) == (executed == "unknown")
    assert ("均未执行" in marker.content) == (executed is False)
    assert scope_interruption(scoped, [request], [result], **kwargs) == scoped
    compacted = [Message(role="system", content="summary"), scoped[-1], marker]
    assert scope_interruption(compacted, [request], [result], **kwargs) == compacted[:2]


@pytest.mark.parametrize("change", ["same", "effort", "window", "route"])
def test_tool_only_legacy_usage_anchor_cannot_cross_input_projection_version(
    tmp_path: Path, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.context.usage_anchor import digest

    from llm_providers import ModelRuntimeConfig, get_model_capabilities

    llm = _FakeLLM(context_window=400000)
    conv, session, _ = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    scope: dict[str, Any] = {
        "session": session.session_id,
        "route": conv._active_provider_route_identity(),
        "reasoning": conv.current_model_config.reasoning_preset,
        "replay_scope": None,
        "attachment_profile": None,
        "defaults": None,
        "responses": "None",
        "input_usage_scope": get_model_capabilities(llm).input_usage_scope,
        "settings": {},
    }
    anchor = replace(
        anchor_for([{"role": "user", "content": "hi"}]),
        version=3,
        compatibility_scope=digest(scope),
    )
    event = Event(
        type="tool_call_request",
        uuid="tool",
        ts=datetime.now(UTC),
        payload={},
        meta={
            "context_usage": {"model": session.current_model, "input_anchor": anchor.payload()},
        },
    )
    session.append(event)
    conv._restore_context_usage()
    assert conv._usage_anchor is not None
    if change == "effort":
        monkeypatch.setattr(
            conv,
            "_active_model_config",
            lambda: ModelRuntimeConfig(session.current_model, "medium"),
        )
        monkeypatch.setattr(conv, "_llm_client_factory", lambda config: llm)
    elif change == "window":
        llm.context_window = 800000
    elif change == "route":
        monkeypatch.setattr(
            conv, "_active_provider_route_identity", lambda client=None: "another route"
        )
    compatible = conv._usage_scope(None, compatibility_only=True)
    assert conv._usage_anchor.version == (3 if change == "route" else 4)
    assert conv._usage_anchor.compatibility_scope != compatible
    result = conv._estimate_context_input([Message(role="user", content="hi")], None)
    assert result.mode == "full_estimate"
    assert result.anchor_tokens == 0
    assert result.tokens < 100
