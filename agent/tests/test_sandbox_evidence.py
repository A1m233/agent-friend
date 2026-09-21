from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.command_runtime.sandbox_evidence import (
    ERROR_CODES,
    SandboxEvidence,
    error_facts,
    state_facts,
)


def recorder(tmp_path: Path) -> SandboxEvidence:
    return SandboxEvidence(
        tmp_path / "logs" / "diagnostics.json", tmp_path / "home", None, "r", "b"
    )


@pytest.mark.parametrize("probe_failure", [False, True])
def test_failure_probe_preserves_original_state_and_error_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_failure: bool
) -> None:
    home = tmp_path / "home"
    evidence = SandboxEvidence(
        tmp_path / "logs/health.json", home, tmp_path / "codex.exe", "r", "b"
    )
    operation = evidence.begin("visibility")

    def probe(executable: Path) -> dict[str, object]:
        persisted = evidence.snapshot()["incidents"][0]
        assert persisted["error"]["nativeCode"] == 1223
        assert persisted["failureHelperProbe"]["status"] == "in_progress"
        assert persisted["stateAfter"]["marker"]["status"] == "missing"
        # Later observations must not overwrite occurrence-time state.
        marker = home / ".sandbox/setup_marker.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"version":5}', encoding="utf-8")
        if probe_failure:
            raise RuntimeError("private loader failure")
        return {"status": "launch_failed", "nativeCode": 126}

    monkeypatch.setattr("agent.command_runtime.sandbox_evidence.probe_setup_helper", probe)
    evidence.finish(
        operation,
        "orchestrator_helper_launch_canceled: ShellExecuteExW failed to launch setup helper: 1223",
    )
    restored = SandboxEvidence(tmp_path / "logs/health.json", home, None, "r", "b")
    record = restored.snapshot()["incidents"][0]
    assert record["error"]["nativeCode"] == 1223
    assert record["stateAfter"]["marker"]["status"] == "missing"
    assert record["failureHelperProbe"]["status"] == (
        "unavailable" if probe_failure else "launch_failed"
    )
    assert "private" not in json.dumps(record)
    assert "finishedAt" in record["failureHelperProbe"]


@pytest.mark.parametrize(
    "stage,detail",
    [("start", None), ("start", "business timeout"), ("repair", "orchestrator_helper_incomplete")],
)
def test_evidence_does_not_probe_healthy_commands_or_reprobe_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, detail: str | None
) -> None:
    evidence = SandboxEvidence(
        tmp_path / "logs/health.json", tmp_path, tmp_path / "codex.exe", "r", "b"
    )

    def unexpected(_: Path) -> dict[str, object]:
        pytest.fail("helper must not be launched")

    monkeypatch.setattr("agent.command_runtime.sandbox_evidence.probe_setup_helper", unexpected)
    evidence.finish(evidence.begin(stage), detail)


def test_native_login_error_is_preserved_without_credentials() -> None:
    facts = error_facts("CreateProcessWithLogonW failed: 1326 private-user password=secret")
    assert facts["nativeCode"] == 1326
    assert "secret" not in json.dumps(facts)


def test_failure_survives_repair_and_restart_without_secret_content(tmp_path: Path) -> None:
    evidence = recorder(tmp_path)
    operation = evidence.begin("start")
    evidence.finish(operation, "orchestrator_helper_incomplete: private command password=secret")
    repair = evidence.begin("repair")
    evidence.repair_started(repair)
    evidence.finish(repair, persist_success=True)
    snapshot = recorder(tmp_path).snapshot()
    assert [r["outcome"] for r in snapshot["incidents"]] == ["failed", "ok"]
    assert snapshot["incidents"][1]["relatedIncidents"] == [operation["id"]]
    assert "private" not in json.dumps(snapshot)
    assert "secret" not in json.dumps(snapshot)
    assert snapshot["incidents"][0]["stateAfter"]["marker"]["status"] == "missing"


@pytest.mark.parametrize(
    "content,status",
    [
        ("{broken", "parse_failed"),
        ('{"version":999}', "version_mismatch"),
        ("[]", "invalid_structure"),
    ],
)
def test_state_failures_are_distinguishable(tmp_path: Path, content: str, status: str) -> None:
    path = tmp_path / ".sandbox" / "setup_marker.json"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    assert state_facts(tmp_path)["marker"]["status"] == status


def test_stale_report_is_not_attributed_and_new_error_preserves_code(tmp_path: Path) -> None:
    path = tmp_path / "home" / ".sandbox" / "setup_error.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"code": "helper_dpapi_protect_failed", "message": "secret"}))
    evidence = recorder(tmp_path)
    operation = evidence.begin("start")
    evidence.finish(operation, "helper_users_file_write_failed (os error 5)")
    record = evidence.snapshot()["incidents"][0]
    assert record["reportAttribution"] == "unconfirmed"
    assert record["error"]["codes"] == ["helper_users_file_write_failed"]
    assert record["error"]["nativeCode"] == 5


def test_concurrent_observations_do_not_claim_unique_attribution(tmp_path: Path) -> None:
    evidence = recorder(tmp_path)
    a = evidence.begin("start")
    b = evidence.begin("visibility")
    evidence.finish(a, "orchestrator_helper_incomplete")
    evidence.finish(b, "orchestrator_helper_incomplete")
    assert all(r["concurrent"] for r in evidence.snapshot()["incidents"])


def test_normal_operations_do_not_evict_failure(tmp_path: Path) -> None:
    evidence = recorder(tmp_path)
    evidence.finish(evidence.begin("start"), "orchestrator_helper_incomplete")
    for _ in range(200):
        evidence.finish(evidence.begin("start"))
    assert len(recorder(tmp_path).snapshot()["incidents"]) == 1


def test_native_cancel_does_not_infer_user_action() -> None:
    facts = error_facts(
        "orchestrator_helper_launch_canceled: ShellExecuteExW failed to launch setup helper: 1223"
    )
    assert facts["nativeCode"] == 1223
    assert "user" not in json.dumps(facts)


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_pinned_native_error_taxonomy_survives_capture(code: str) -> None:
    assert error_facts(f"{code}: sensitive text")["codes"] == [code]


def test_retention_eviction_is_durable_and_old_incident_cannot_hide_new(tmp_path: Path) -> None:
    evidence = recorder(tmp_path)
    for _ in range(34):
        evidence.finish(evidence.begin("start"), "orchestrator_helper_incomplete")
    restored = recorder(tmp_path)
    snapshot = restored.snapshot()
    assert len(snapshot["incidents"]) == 32
    assert "retention_evicted" in snapshot["gaps"]
    assert restored.directory is not None
    (restored.directory / "000-broken.json").write_text("{broken", encoding="utf-8")
    assert len(restored.snapshot()["incidents"]) == 32


def test_new_report_is_distinguished_from_old_report(tmp_path: Path) -> None:
    evidence = recorder(tmp_path)
    operation = evidence.begin("start")
    path = tmp_path / "home" / ".sandbox" / "setup_error.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"code": "helper_firewall_policy_access_failed", "message": "os error 5"})
    )
    import os

    os.utime(path, ns=(operation["startedNs"] + 1, operation["startedNs"] + 1))
    evidence.finish(operation, "helper_firewall_policy_access_failed")
    record = evidence.snapshot()["incidents"][0]
    assert record["reportAttribution"] == "changed_during_operation"
    assert record["reportAfter"]["code"] == "helper_firewall_policy_access_failed"


def test_disk_failure_does_not_replace_business_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = recorder(tmp_path)
    operation = evidence.begin("start")
    import os

    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk failed")))
    evidence.finish(operation, "orchestrator_helper_incomplete")
    assert "write_failed" in evidence.snapshot()["gaps"]
