from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.sessions.continuation import continuation_target
from agent.sessions.recovery import (
    RecoveredToolResult,
    RecoveryProbe,
    RecoveryProbeOutcome,
    ToolTransactionReconciler,
)

from agent import AgentError, Event, EventType, JsonlSessionStore, Session


def event(kind: EventType, run: str = "old", **payload: object) -> Event:
    return Event(
        type=kind,
        uuid=f"{kind}-{run}",
        ts=datetime.now(UTC),
        payload=dict(payload),
        meta={"run_id": run},
    )


def history() -> list[Event]:
    return [
        event("run_started"),
        event("user_message", content="finish the task"),
        event("run_error", recoverable=True),
        event("run_terminal", status="failed"),
    ]


def test_terminal_and_new_start_do_not_hide_recoverable_source() -> None:
    events = [*history(), event("run_started", "new")]
    target = continuation_target(events, "old", current_run_id="new")
    assert target.source_run_id == target.root_run_id == "old"
    assert target.user_event.payload["content"] == "finish the task"


def test_stale_source_and_new_user_input_are_rejected() -> None:
    with pytest.raises(AgentError):
        continuation_target(history(), "another-run")
    with pytest.raises(AgentError):
        continuation_target([*history(), event("user_message", "new", content="new task")], "old")


def test_unclosed_tool_is_not_relaunched() -> None:
    events = history()
    events.insert(2, event("tool_call_request", tool_call_id="pending"))
    with pytest.raises(AgentError, match="工具执行结果待确认"):
        continuation_target(events)


def test_repeated_recovery_targets_latest_attempt_and_preserves_root() -> None:
    events = [
        *history(),
        event("run_started", "retry", source_run_id="old", root_run_id="old"),
        event("run_error", "retry", recoverable=True),
        event("run_terminal", "retry", status="failed"),
    ]
    with pytest.raises(AgentError):
        continuation_target(events, "old")
    assert continuation_target(events, "retry").root_run_id == "old"


def test_restart_and_user_stop_allow_explicit_continuation_but_completed_does_not() -> None:
    for reason in ("explicit_request", "computer_or_owner_runtime_lost"):
        events = [
            event("user_message"),
            event("run_terminal", status="interrupted", reason_code=reason),
        ]
        assert continuation_target(events).source_run_id == "old"
    with pytest.raises(AgentError):
        continuation_target([event("user_message"), event("run_terminal", status="completed")])


@pytest.mark.parametrize("outcome", list(RecoveryProbeOutcome))
def test_restart_recovery_eligibility_follows_real_transaction_reconciliation(
    tmp_path: Path, outcome: RecoveryProbeOutcome
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="recovery", persona="default", model="test")
    store.create(session)
    for entry in [
        event("run_started", run_id="old"),
        event("user_message", content="finish"),
        event("tool_call_request", tool_call_id="call", tool_name="action"),
    ]:
        store.append_event(
            session.session_id,
            replace(entry, meta={**entry.meta, "owner_runtime_instance_id": "old-owner"}),
        )

    class Adapter:
        calls = 0

        def probe(self, **kwargs: object) -> RecoveryProbe:
            self.calls += 1
            return RecoveryProbe(
                outcome,
                result=RecoveredToolResult(content="saved", is_error=False, executed=True)
                if outcome is RecoveryProbeOutcome.COMPLETED
                else None,
            )

    adapter = Adapter()
    report = ToolTransactionReconciler(
        store, runtime_instance_id="new-owner", adapters={"action": adapter}
    ).reconcile_session(session.session_id)
    assert adapter.calls == 1
    if outcome in {RecoveryProbeOutcome.RUNNING, RecoveryProbeOutcome.UNKNOWN}:
        with pytest.raises(AgentError):
            continuation_target(report.session.active_events)
        assert not any(e.type == "tool_call_result" for e in report.session.active_events)
    else:
        assert continuation_target(report.session.active_events).source_run_id == "old"
        result = next(e for e in report.session.active_events if e.type == "tool_call_result")
        if outcome is RecoveryProbeOutcome.LOST:
            assert result.payload["executed"] == "unknown"
            assert result.payload["semantic_status"] == "unknown"
        else:
            assert result.payload["content"] == "saved"
