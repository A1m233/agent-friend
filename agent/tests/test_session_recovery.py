from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.sessions import (
    RecoveredToolResult,
    RecoveryProbe,
    RecoveryProbeOutcome,
    SessionModelHistory,
    ToolExecutionEvidence,
    ToolExecutionEvidenceStore,
    ToolTransactionReconciler,
    render_interrupted_evidence,
)

from agent import Event, JsonlSessionStore, Session


def test_reconcile_reuses_unchanged_loaded_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    loaded = store.load(session_id)

    def unexpected_load(*args: object, **kwargs: object) -> Session:
        raise AssertionError("unchanged input must not be parsed twice")

    monkeypatch.setattr(store, "load", unexpected_load)
    report = ToolTransactionReconciler(store, runtime_instance_id="new").reconcile_session(
        session_id, loaded_session=loaded
    )
    assert report.session is loaded
    assert report.interrupted_tool_calls == 1
    assert loaded.loaded_source_version is None


@pytest.mark.parametrize("changed_on_disk", [True, False])
def test_reconcile_reloads_changed_input(tmp_path: Path, changed_on_disk: bool) -> None:
    store, session_id = _store_with_request(tmp_path)
    loaded = store.load(session_id)
    extra = _event("user_message", "new-message", {"content": "next"})
    if changed_on_disk:
        # An independent writer changed the file after the initial read.
        JsonlSessionStore(tmp_path / "sessions").append_event(session_id, extra)
    else:
        loaded.append(extra)
    report = ToolTransactionReconciler(store, runtime_instance_id="new").reconcile_session(
        session_id, loaded_session=loaded
    )
    assert report.session is not loaded
    assert any(e.uuid == "new-message" for e in report.session.events) is changed_on_disk


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
    evidence_ref: str | None = None,
    tool_name: str = "exec_command",
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
            "tool_name": tool_name,
            "args": {},
        },
    )
    store.append_event(session.session_id, request)
    if checkpoint_mode is not None:
        checkpoint_payload: dict[str, object] = {
            "tool_call_id": "call-1",
            "tool_name": tool_name,
            "recovery_mode": checkpoint_mode,
            "operation_id": "proc_1",
            "execution_state": "launch_committed",
        }
        if evidence_ref is not None:
            checkpoint_payload["evidence_ref"] = evidence_ref
        store.append_event(
            session.session_id,
            _event(
                "tool_execution_checkpoint",
                "checkpoint-1",
                checkpoint_payload,
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


def test_model_only_lost_owner_notifies_after_durable_terminal_once(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="model only", persona="default", model="fake")
    store.create(session)
    store.append_event(
        session.session_id, _event("run_started", "started", {"run_id": "r"}, run_id="r")
    )
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="new")
    seen: list[str] = []

    def observed(current: Session, terminal: Event) -> None:
        assert any(item.uuid == terminal.uuid for item in store.load(current.session_id).events)
        seen.append(str(terminal.payload["run_id"]))

    reconciler.add_terminal_observer(observed)
    assert reconciler.reconcile_session(session.session_id).appended_run_terminals == 1
    assert reconciler.reconcile_session(session.session_id).appended_run_terminals == 0
    assert seen == ["r"]


def test_model_only_current_owner_is_not_declared_lost(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="model only", persona="default", model="fake")
    store.create(session)
    store.append_event(
        session.session_id,
        _event("run_started", "started", {"run_id": "r"}, run_id="r", owner="current"),
    )
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="current")
    assert reconciler.reconcile_session(session.session_id).appended_run_terminals == 0


def test_reconciler_restores_persisted_partial_command_output_without_reexecution(
    tmp_path: Path,
) -> None:
    evidence_store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    evidence_ref = evidence_store.new_reference()
    store, session_id = _store_with_request(
        tmp_path,
        checkpoint_mode="resident_only",
        evidence_ref=evidence_ref,
    )
    evidence = ToolExecutionEvidence(
        evidence_ref=evidence_ref,
        session_id=session_id,
        run_id="run-1",
        tool_call_id="call-1",
        operation_id="proc_1",
        tool_name="exec_command",
        process_status="running",
        output_seq=5,
        dropped_bytes=11,
        stdout="before bridge restart",
        stderr="partial stderr",
        captured_at=datetime.now(UTC).isoformat(),
    )
    evidence_store.write(evidence)
    reconciler = ToolTransactionReconciler(
        store,
        runtime_instance_id="runtime-new",
        tool_execution_evidence_store=evidence_store,
    )

    first = reconciler.reconcile_session(session_id)
    second = reconciler.reconcile_session(session_id)

    assert first.interrupted_tool_calls == 1
    assert second.interrupted_tool_calls == 0
    results = [event for event in second.session.events if event.type == "tool_call_result"]
    assert len(results) == 1
    result = results[0]
    assert result.payload["content"] == render_interrupted_evidence(evidence)
    content = json.loads(str(result.payload["content"]))
    assert result.payload["evidence_ref"] == evidence_ref
    assert result.payload["semantic_status"] == "unknown"
    assert result.payload["process_status"] == "runtime_lost"
    assert content["stdout"] == "before bridge restart"
    assert content["stderr"] == "partial stderr"
    assert content["partial"] is True
    assert content["complete"] is False
    assert content["evidence_ref"] == evidence_ref


def test_reconciler_rejects_corrupt_evidence_and_falls_back_to_unknown(
    tmp_path: Path,
) -> None:
    evidence_store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    evidence_ref = evidence_store.new_reference()
    store, session_id = _store_with_request(
        tmp_path,
        checkpoint_mode="resident_only",
        evidence_ref=evidence_ref,
    )
    evidence_store.write(
        ToolExecutionEvidence(
            evidence_ref=evidence_ref,
            session_id=session_id,
            run_id="run-1",
            tool_call_id="call-1",
            operation_id="proc_1",
            tool_name="exec_command",
            process_status="running",
            output_seq=1,
            dropped_bytes=0,
            stdout="partial",
            captured_at=datetime.now(UTC).isoformat(),
        )
    )
    evidence_file = next((tmp_path / "evidence").rglob("*.json"))
    evidence_file.write_text("{broken", encoding="utf-8")
    reconciler = ToolTransactionReconciler(
        store,
        runtime_instance_id="runtime-new",
        tool_execution_evidence_store=evidence_store,
    )

    report = reconciler.reconcile_session(session_id)

    result = next(event for event in report.session.events if event.type == "tool_call_result")
    assert result.payload.get("evidence_ref") is None
    assert "领域结果均未知" in str(result.payload["content"])


@pytest.mark.parametrize("status", ["accepted", "dispatching"])
def test_application_recovery_preserves_acceptance_without_process_ownership(
    tmp_path: Path, status: str
) -> None:
    evidence_store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    reference = evidence_store.new_reference()
    store, session_id = _store_with_request(
        tmp_path,
        checkpoint_mode="persisted_evidence",
        evidence_ref=reference,
        tool_name="launch_desktop_application",
    )
    evidence_store.write(
        ToolExecutionEvidence(
            evidence_ref=reference,
            session_id=session_id,
            run_id="run-1",
            tool_call_id="call-1",
            operation_id="proc_1",
            tool_name="launch_desktop_application",
            process_status=status,
            output_seq=1,
            dropped_bytes=0,
            kind="application_launch",
            stdout=json.dumps(
                {"status": status, "lifecycle": "user_desktop", "readiness": "not_verified"}
            ),
        )
    )
    reconciler = ToolTransactionReconciler(
        store, runtime_instance_id="new", tool_execution_evidence_store=evidence_store
    )
    reconciler.reconcile_session(session_id)
    session = reconciler.reconcile_session(session_id).session
    results = [event.payload for event in session.events if event.type == "tool_call_result"]
    assert len(results) == 1
    assert json.loads(str(results[0]["content"]))["status"] == (
        "accepted" if status == "accepted" else "unknown"
    )
    assert results[0]["process_status"] == "not_managed"
    assert results[0]["semantic_status"] == "not_verified"
    assert results[0]["is_error"] is (status != "accepted")


def test_reconciler_closes_orphan_even_when_run_terminal_already_exists(tmp_path: Path) -> None:
    store, session_id = _store_with_request(tmp_path, checkpoint_mode="resident_only")
    store.append_event(
        session_id,
        _event(
            "run_terminal",
            "terminal-1",
            {
                "run_id": "run-1",
                "status": "interrupted",
                "reason_code": "explicit_request",
                "source": "runtime",
                "recovered": False,
            },
        ),
    )
    reconciler = ToolTransactionReconciler(store, runtime_instance_id="runtime-new")

    report = reconciler.reconcile_session(session_id)

    assert report.interrupted_tool_calls == 1
    assert report.appended_run_terminals == 0
    assert sum(event.type == "tool_call_result" for event in report.session.events) == 1
    assert sum(event.type == "run_terminal" for event in report.session.events) == 1


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
