"""Bounded command evidence persistence and rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.command_runtime import CommandProcessSnapshot
from agent.sessions import (
    ToolExecutionEvidenceError,
    ToolExecutionEvidenceStore,
    render_interrupted_evidence,
)


def _snapshot(
    evidence_ref: str,
    *,
    stdout: bytes = b"partial stdout",
    stderr: bytes = b"partial stderr",
    output_seq: int = 2,
    status: str = "running",
) -> CommandProcessSnapshot:
    return CommandProcessSnapshot(
        process_id="proc_evidence",
        session_id="session-a",
        run_id="run-a",
        tool_call_id="event-call-a",
        tool_name="exec_command",
        mode="buffered",
        evidence_ref=evidence_ref,
        status=status,  # type: ignore[arg-type]
        stdout_tail=stdout,
        stderr_tail=stderr,
        output_seq=output_seq,
    )


def test_evidence_store_atomically_replaces_latest_bounded_snapshot(tmp_path: Path) -> None:
    store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    evidence_ref = store.new_reference()

    assert store.persist_process_snapshot(_snapshot(evidence_ref)) is True
    assert (
        store.persist_process_snapshot(
            _snapshot(evidence_ref, stdout=b"newest stdout", output_seq=3),
            force=True,
        )
        is True
    )

    recovered = store.load("session-a", evidence_ref)
    assert recovered is not None
    assert recovered.stdout == "newest stdout"
    assert recovered.stderr == "partial stderr"
    assert recovered.output_seq == 3
    assert recovered.complete is False
    assert len(list((tmp_path / "evidence").rglob("*.json"))) == 1
    assert list((tmp_path / "evidence").rglob("*.tmp")) == []


def test_evidence_store_rejects_corrupt_content_and_owner_mismatch(tmp_path: Path) -> None:
    store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    evidence_ref = store.new_reference()
    store.persist_process_snapshot(_snapshot(evidence_ref), force=True)
    evidence_file = next((tmp_path / "evidence").rglob("*.json"))
    record = json.loads(evidence_file.read_text(encoding="utf-8"))
    record["stdout"] = "tampered"
    evidence_file.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ToolExecutionEvidenceError, match="hash mismatch"):
        store.load("session-a", evidence_ref)
    assert store.load("other-session", evidence_ref) is None


def test_interrupted_evidence_is_partial_unknown_and_inline_bounded(tmp_path: Path) -> None:
    store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    evidence_ref = store.new_reference()
    store.persist_process_snapshot(
        _snapshot(
            evidence_ref,
            stdout=b"a" * (256 * 1024),
            stderr=b"b" * (256 * 1024),
            output_seq=9,
            status="cancelled",
        ),
        force=True,
    )
    evidence = store.load("session-a", evidence_ref)
    assert evidence is not None

    rendered = render_interrupted_evidence(evidence)
    assert rendered is not None
    payload = json.loads(rendered)
    assert payload["status"] == "interrupted"
    assert payload["semantic_status"] == "unknown"
    assert payload["partial"] is True
    assert payload["complete"] is False
    assert payload["evidence_ref"] == evidence_ref
    assert payload["output_seq"] == 9
    assert payload["dropped_bytes"] > 0
    assert len(payload["stdout"].encode("utf-8")) <= 32 * 1024
    assert len(payload["stderr"].encode("utf-8")) <= 32 * 1024


def test_evidence_store_quota_failure_does_not_delete_existing_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    store = ToolExecutionEvidenceStore(root, max_session_bytes=2_000)
    first_ref = store.new_reference()
    store.persist_process_snapshot(_snapshot(first_ref), force=True)
    second_ref = store.new_reference()

    with pytest.raises(ToolExecutionEvidenceError, match="quota exceeded"):
        store.persist_process_snapshot(
            _snapshot(second_ref, stdout=b"x" * 4_000),
            force=True,
        )

    assert store.load("session-a", first_ref) is not None
    assert store.load("session-a", second_ref) is None


def test_delete_session_removes_only_that_sessions_evidence(tmp_path: Path) -> None:
    store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    first_ref = store.new_reference()
    second_ref = store.new_reference()
    store.persist_process_snapshot(_snapshot(first_ref), force=True)
    other = _snapshot(second_ref)
    other.session_id = "session-b"
    store.persist_process_snapshot(other, force=True)

    store.delete_session("session-a")

    assert store.load("session-a", first_ref) is None
    assert store.load("session-b", second_ref) is not None
