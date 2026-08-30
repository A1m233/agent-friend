from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from agent.sessions import (
    RecoveredToolResult,
    RecoveryProbe,
    RecoveryProbeOutcome,
    SessionModelHistory,
    ToolTransactionReconciler,
)

from agent import Event, JsonlSessionStore, Session


def _event(
    type_: str,
    uuid: str,
    payload: dict[str, object],
    *,
    run_id: str = "run-1",
    owner: str = "runtime-old",
) -> Event:
    return Event(
        type=type_,  # type: ignore[arg-type]
        uuid=uuid,
        ts=datetime.now(UTC),
        payload=payload,
        meta={"run_id": run_id, "owner_runtime_instance_id": owner},
    )


def _store_with_request(
    tmp_path: Path,
    *,
    checkpoint_mode: str | None = None,
) -> tuple[JsonlSessionStore, str]:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="t", persona="default", model="m")
    store.create(session)
    request = _event(
        "tool_call_request",
        "req-1",
        {
            "tool_call_id": "call-1",
            "event_tool_call_id": "event-call-1",
            "tool_name": "exec_command",
            "args": {},
        },
    )
    store.append_event(session.session_id, request)
    if checkpoint_mode is not None:
        store.append_event(
            session.session_id,
            _event(
                "tool_execution_checkpoint",
                "checkpoint-1",
                {
                    "tool_call_id": "call-1",
                    "tool_name": "exec_command",
                    "recovery_mode": checkpoint_mode,
                    "operation_id": "proc_1",
                    "execution_state": "launch_committed",
                },
            ),
        )
    return store, session.session_id


def test_reconciler_appends_interrupted_result_and_run_terminal(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-new")

    first = reconciler.reconcile_session(session_id)
    second = reconciler.reconcile_session(session_id)

    assert first.interrupted_tool_calls == 1
    assert first.appended_run_terminals == 1
    assert second.interrupted_tool_calls == 0
    assert second.appended_run_terminals == 0
    results = [event for event in second.session.events if event.type == "tool_call_result"]
    terminals = [event for event in second.session.events if event.type == "run_terminal"]
    assert len(results) == 1
    assert len(terminals) == 1
    assert results[0].payload["transaction_status"] == "interrupted"
    assert results[0].payload["process_status"] == "runtime_lost"
    assert results[0].payload["semantic_status"] == "unknown"
    assert results[0].payload["executed"] == "unknown"


def test_reconciler_preserves_unknown_queryable_transaction(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="queryable")
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-new")

    report = reconciler.reconcile_session(session_id)

    assert report.unknown_tool_calls == 1
    assert not any(event.type == "tool_call_result" for event in report.session.events)
    assert not any(event.type == "run_terminal" for event in report.session.events)


def test_reconciler_keeps_current_owner_without_live_proof_unknown(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-old")

    report = reconciler.reconcile_session(session_id)

    assert report.unknown_tool_calls == 1
    assert report.interrupted_tool_calls == 0


class _CompletedAdapter:
    def probe(self, **_kwargs: object) -> RecoveryProbe:
        return RecoveryProbe(
            RecoveryProbeOutcome.COMPLETED,
            result=RecoveredToolResult(
                content="recovered",
                is_error=False,
                process_status="exited",
                semantic_status="succeeded",
                executed=True,
            ),
        )


def test_reconciler_appends_recovered_result_from_adapter(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="queryable")
    reconciler = ToolTransactionReconciler(
        store,
        runtime_instance_id="runtime-new",
        adapters={"exec_command": _CompletedAdapter()},
    )

    report = reconciler.reconcile_session(session_id)

    assert report.recovered_tool_calls == 1
    result = next(event for event in report.session.events if event.type == "tool_call_result")
    assert result.payload["content"] == "recovered"
    assert result.payload["result_origin"] == "host_recovered_result"


def test_reconciler_only_adds_terminal_after_result_was_already_committed(
    tmp_path: Path,
) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    store.append_event(
        session_id,
        _event(
            "tool_call_result",
            "result-1",
            {
                "tool_call_id": "call-1",
                "tool_name": "exec_command",
                "content": "done",
                "is_error": False,
            },
        ),
    )
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-new")

    report = reconciler.reconcile_session(session_id)

    assert report.interrupted_tool_calls == 0
    assert report.appended_run_terminals == 1
    assert sum(event.type == "tool_call_result" for event in report.session.events) == 1
    assert sum(event.type == "run_terminal" for event in report.session.events) == 1


def test_background_and_lazy_reconciliation_share_idempotent_guard(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-new")

    reports = reconciler.reconcile_known_sessions()
    lazy = reconciler.reconcile_session(session_id)

    assert sum(report.interrupted_tool_calls for report in reports) == 1
    assert lazy.interrupted_tool_calls == 0
    assert sum(event.type == "tool_call_result" for event in lazy.session.events) == 1


def test_concurrent_reconciliation_writes_only_one_result_and_terminal(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-new")

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(reconciler.reconcile_session, [session_id, session_id]))

    final = store.load(session_id)
    assert sum(report.interrupted_tool_calls for report in reports) == 1
    assert sum(event.type == "tool_call_result" for event in final.events) == 1
    assert sum(event.type == "run_terminal" for event in final.events) == 1


def test_business_model_history_excludes_persisted_model_only_items() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.append(_event("user_message", "user-1", {"content": "hello"}))
    session.append(
        _event(
            "skill_context",
            "skill-1",
            {"rendered": "private model-only skill instructions"},
        )
    )

    history = SessionModelHistory.from_business_session(
        session,
        reason="process_resume_degraded",
    )

    assert [(message.role, message.content) for message in history.items] == [("user", "hello")]
    assert any("private model-only" in message.content for message in session.llm_messages)
