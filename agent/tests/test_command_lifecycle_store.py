from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.command_runtime.lifecycle.store import LifecycleStore, LifecycleUnavailable


@pytest.mark.parametrize("directory", ["home/journal", "exec/journal"])
def test_runtime_journal_cannot_live_inside_executor_writable_roots(
    tmp_path: Path, directory: str
) -> None:
    from agent.command_runtime import CommandRuntime

    with pytest.raises(ValueError, match="outside"):
        CommandRuntime(
            None,
            codex_home=tmp_path / "home",
            execution_root=tmp_path / "exec",
            lifecycle_dir=tmp_path / directory,
        )


def test_restart_preserves_uncertain_work_and_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agent.command_runtime.lifecycle.store.uptime_ms", lambda: 100_000)
    first = LifecycleStore("first", tmp_path / "journal")
    assert first.error is None
    first.begin("proc_unknown", uptime_ms=100_000)
    first.change(
        "proc_unknown",
        phase="starting",
        execution_result="unknown",
        resource_state="unverified",
        reconciliation_required=True,
        failure="start_timeout",
    )
    first.change("proc_unknown", failure="connection_lost")
    first.close()
    second = LifecycleStore("second", tmp_path / "journal")
    try:
        assert second.error is None
        record = second.blockers()[0]
        assert record.phase == "finished"
        assert record.execution_result == "unknown"
        assert record.first_failure == "start_timeout"
        assert record.failures == ["start_timeout", "connection_lost"]
        assert record.resource_evidence == "previous_runtime_unconfirmed"
    finally:
        second.close()


def test_proven_system_restart_closes_resources_without_rewriting_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = LifecycleStore("first", tmp_path / "journal")
    first.begin("proc_unknown", uptime_ms=100_000)
    first.change(
        "proc_unknown",
        phase="starting",
        execution_result="unknown",
        resource_state="unverified",
        reconciliation_required=True,
    )
    first.close()
    monkeypatch.setattr("agent.command_runtime.lifecycle.store.uptime_ms", lambda: 5_000)
    second = LifecycleStore("second", tmp_path / "journal")
    try:
        record = second.get("proc_unknown")
        assert record is not None and record.resource_state == "closed"
        assert record.execution_result == "unknown"
        assert not second.blockers(maintenance=True)
    finally:
        second.close()


def test_second_owner_cannot_overwrite_journal(tmp_path: Path) -> None:
    first = LifecycleStore("first", tmp_path / "journal")
    first.begin("proc_original")
    second = LifecycleStore("second", tmp_path / "journal")
    try:
        assert second.error == "lifecycle_owner_active"
        with pytest.raises(LifecycleUnavailable):
            second.begin("proc_replacement")
        assert first.get("proc_original") is not None
    finally:
        second.close()
        first.close()


def test_closed_owner_cannot_overwrite_a_restarted_runtime(tmp_path: Path) -> None:
    first = LifecycleStore("first", tmp_path / "journal")
    first.begin("proc_old")
    first.close()
    second = LifecycleStore("second", tmp_path / "journal")
    try:
        second.begin("proc_new")
        with pytest.raises(LifecycleUnavailable, match="closed"):
            first.change("proc_old", phase="finished")
    finally:
        second.close()


def test_corrupt_journal_and_failed_write_do_not_allow_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = LifecycleStore("first", tmp_path / "journal")
    first.begin("proc_saved")
    first.close()
    (tmp_path / "journal/state.json").write_text(
        '{"schemaVersion":999,"records":[]}', encoding="utf-8"
    )
    corrupt = LifecycleStore("second", tmp_path / "journal")
    try:
        with pytest.raises(LifecycleUnavailable):
            corrupt.begin("proc_no_dispatch")
    finally:
        corrupt.close()
    memory = LifecycleStore("memory", tmp_path / "another")

    def fail(*_: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("agent.command_runtime.lifecycle.store.os.replace", fail)
    try:
        with pytest.raises(LifecycleUnavailable, match="write_failed"):
            memory.begin("proc_never_sent")
        with pytest.raises(LifecycleUnavailable):
            memory.begin("proc_no_retry")
    finally:
        memory.close()


def test_journal_excludes_command_material_and_protects_unresolved_records(tmp_path: Path) -> None:
    store = LifecycleStore("runtime", tmp_path / "journal")
    try:
        store.begin("proc_unknown")
        store.change("proc_unknown", resource_state="unverified", reconciliation_required=True)
        for index in range(140):
            operation = f"proc_{index}"
            store.begin(operation)
            store.change(operation, phase="finished", resource_state="closed")
        assert store.get("proc_unknown") is not None
        assert len(store.records()) < 140
        data = json.loads((tmp_path / "journal/state.json").read_text(encoding="utf-8"))
        assert all("argv" not in record and "env" not in record for record in data["records"])
        with pytest.raises(ValueError):
            store.change("proc_unknown", failure="secret command text")
    finally:
        store.close()
