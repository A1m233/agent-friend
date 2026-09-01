"""040：已清理 tool result 的当前会话有界恢复。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent.sessions import (
    Event,
    JsonlSessionStore,
    Session,
    ToolExecutionEvidence,
    ToolExecutionEvidenceStore,
)
from agent.tools.builtin.tool_result_recovery import ToolResultRecoveryTool

_CURRENT_SESSION_ID_ARG = "__agent_friend_current_session_id"


def _create_session(store: JsonlSessionStore, session_id: str) -> Session:
    session = Session.new(
        title="t",
        persona="default",
        model="m",
        session_id=session_id,
    )
    store.create(session)
    return session


def _append_result(
    store: JsonlSessionStore,
    session: Session,
    *,
    result_ref: str,
    content: str,
) -> Event:
    event = Event(
        type="tool_call_result",
        uuid=result_ref,
        ts=datetime.now(UTC),
        payload={
            "tool_call_id": "call-1",
            "tool_name": "web_search",
            "content": content,
            "is_error": False,
        },
    )
    store.append_event(session.session_id, event)
    session.append(event)
    return event


def test_recovery_is_current_session_scoped_and_query_bounded(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    current = _create_session(store, "current")
    other = _create_session(store, "other")
    content = "A" * 2_000 + "needle exact detail" + "B" * 8_000
    _append_result(store, current, result_ref="current-result", content=content)
    _append_result(store, other, result_ref="other-result", content="secret")
    tool = ToolResultRecoveryTool(store)

    recovered = tool.invoke(
        {
            "result_ref": "current-result",
            "query": "needle exact detail",
            "max_chars": 1_000,
            _CURRENT_SESSION_ID_ARG: current.session_id,
        }
    )
    assert recovered.is_error is False
    assert "needle exact detail" in recovered.text
    assert "original_chars=10019" in recovered.text
    assert len(recovered.text) < 1_300
    assert recovered.meta["recovered_result_ref"] == "current-result"

    blocked = tool.invoke(
        {
            "result_ref": "other-result",
            _CURRENT_SESSION_ID_ARG: current.session_id,
        }
    )
    assert blocked.is_error is True
    assert "当前会话" in blocked.text


def test_recovery_rejects_result_invalidated_by_turn_rewrite(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session = _create_session(store, "current")
    result = _append_result(store, session, result_ref="old-result", content="old content")
    rewrite = Event(
        type="turn_rewrite",
        uuid="rewrite-1",
        ts=datetime.now(UTC),
        payload={"inactive_event_uuids": [result.uuid]},
    )
    store.append_event(session.session_id, rewrite)
    session.append(rewrite)

    recovered = ToolResultRecoveryTool(store).invoke(
        {
            "result_ref": result.uuid,
            _CURRENT_SESSION_ID_ARG: session.session_id,
        }
    )

    assert recovered.is_error is True
    assert "有效历史" in recovered.text


def test_recovery_reads_only_evidence_referenced_by_current_active_result(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    evidence_store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    current = _create_session(store, "current")
    other = _create_session(store, "other")
    current_ref = evidence_store.new_reference()
    other_ref = evidence_store.new_reference()
    for session, evidence_ref, stdout in (
        (current, current_ref, "prefix needle suffix"),
        (other, other_ref, "other session secret"),
    ):
        evidence_store.write(
            ToolExecutionEvidence(
                evidence_ref=evidence_ref,
                session_id=session.session_id,
                run_id="run-1",
                tool_call_id="call-1",
                operation_id="proc-1",
                tool_name="exec_command",
                process_status="cancelled",
                output_seq=1,
                dropped_bytes=0,
                stdout=stdout,
                captured_at=datetime.now(UTC).isoformat(),
            )
        )
        result = Event(
            type="tool_call_result",
            uuid=f"result-{session.session_id}",
            ts=datetime.now(UTC),
            payload={
                "tool_call_id": "call-1",
                "event_tool_call_id": "call-1",
                "tool_name": "exec_command",
                "content": "partial preview",
                "is_error": True,
                "evidence_ref": evidence_ref,
            },
            meta={"run_id": "run-1"},
        )
        store.append_event(session.session_id, result)
        session.append(result)
    tool = ToolResultRecoveryTool(store, evidence_store)

    recovered = tool.invoke(
        {
            "evidence_ref": current_ref,
            "query": "needle",
            _CURRENT_SESSION_ID_ARG: current.session_id,
        }
    )
    blocked = tool.invoke(
        {
            "evidence_ref": other_ref,
            _CURRENT_SESSION_ID_ARG: current.session_id,
        }
    )

    assert recovered.is_error is False
    assert "needle" in recovered.text
    assert f"evidence_ref={current_ref}" in recovered.text
    assert recovered.meta["recovered_evidence_ref"] == current_ref
    assert blocked.is_error is True
    assert "当前会话" in blocked.text
