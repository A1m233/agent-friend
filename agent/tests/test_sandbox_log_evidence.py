from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agent.command_runtime.sandbox_evidence import SandboxEvidence
from agent.command_runtime.sandbox_log_evidence import MAX_BYTES, log_cursor, log_window


def make_log(tmp_path: Path, text: str = "") -> Path:
    path = tmp_path / ".sandbox" / "sandbox.2026-09-18.log"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_only_new_signals_are_exported_without_private_content(tmp_path: Path) -> None:
    path = make_log(tmp_path, "setup refresh: exited with status 1\n")
    cursor = log_cursor(tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "sandbox setup required: offline firewall settings changed (stored_ports=[1234])\n"
            "DEBUG: sandbox user login failed; deleting C:/private/sandbox_users.json\n"
            "[2026-09-18 02:11:03.000 codex.exe] setup refresh: setup refresh failed to launch helper: helper=C:/private/app.exe, os error 5\n"
            "START: echo sandbox setup required: sandbox setup marker missing or incompatible\n"
        )
    result = log_window(tmp_path, cursor)
    record = result["records"][0]
    assert record["scope"] == "appended"
    assert [e["signal"] for e in record["events"]] == [
        "setup_firewall_settings_changed",
        "users_delete_after_login_failure",
        "refresh_launch_failed",
    ]
    assert record["events"][-1]["nativeCode"] == 5
    for secret in ("private", "1234", "sandbox_users", "app.exe"):
        assert secret not in json.dumps(result)


def test_replaced_log_is_not_claimed_as_current_operation(tmp_path: Path) -> None:
    path = make_log(tmp_path, "original long line\n")
    cursor = log_cursor(tmp_path)
    path.write_text(
        "sandbox setup required: sandbox users missing or incompatible with marker version\n"
    )
    result = log_window(tmp_path, cursor)
    assert result["records"][0]["scope"] == "unattributed_tail"
    assert "new_or_replaced_log" in result["gaps"]


def test_bounded_tail_does_not_interpret_partial_lines(tmp_path: Path) -> None:
    path = make_log(tmp_path)
    cursor = log_cursor(tmp_path)
    path.write_bytes(
        b"x" * MAX_BYTES
        + b"setup refresh: spawning private\n" * 100
        + b"setup refresh: spawning unfinished"
    )
    result = log_window(tmp_path, cursor)
    assert {"byte_limit", "event_limit", "partial_line"} <= set(result["gaps"])
    assert len(result["records"][0]["events"]) == 64


def test_log_read_failure_and_missing_baseline_are_explicit(tmp_path: Path) -> None:
    path = make_log(tmp_path)
    cursor = log_cursor(tmp_path)
    path.unlink()
    result = log_window(tmp_path, cursor)
    assert {"baseline_log_missing", "logs_unavailable"} <= set(result["gaps"])
    path.mkdir()
    assert log_cursor(tmp_path)["status"] == "read_failed"
    assert "read_failed" in log_window(tmp_path, cursor)["gaps"]


def test_truncation_during_read_is_not_reported_as_empty_healthy_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_log(tmp_path)
    cursor = log_cursor(tmp_path)
    path.write_text("setup refresh: spawning helper\n", encoding="utf-8")
    original = os.fstat

    def truncate_after_stat(fd: int) -> os.stat_result:
        result = original(fd)
        path.write_bytes(b"")
        return result

    monkeypatch.setattr(os, "fstat", truncate_after_stat)
    assert "changed_during_read" in log_window(tmp_path, cursor)["gaps"]


def test_incident_retains_window_across_restart_but_not_private_cursor(tmp_path: Path) -> None:
    path = make_log(tmp_path)
    evidence = SandboxEvidence(tmp_path / "logs" / "health.json", tmp_path, None, "r", "b")
    operation = evidence.begin("visibility")
    path.write_text(
        "sandbox setup required: sandbox users missing or incompatible with marker version\n"
    )
    evidence.finish(operation, "orchestrator_helper_launch_canceled: launch setup helper: 1223")
    restored = SandboxEvidence(tmp_path / "logs" / "health.json", tmp_path, None, "r", "b")
    record = restored.snapshot()["incidents"][0]
    assert (
        record["logWindow"]["records"][0]["events"][0]["signal"]
        == "setup_users_missing_or_incompatible"
    )
    assert path.name not in json.dumps(record)
    assert not evidence._log_cursors
    healthy = evidence.begin("start")
    evidence.finish(healthy)
    assert not evidence._log_cursors
