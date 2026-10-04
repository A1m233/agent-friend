from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from startup_evidence import digest  # noqa: E402
from startup_journal import analyze  # noqa: E402
from startup_verify import measure  # noqa: E402


def test_installed_network_evidence_rejects_production_and_keeps_truncation(tmp_path):
    from installed_evidence import summarize_network

    path = tmp_path / "netlog.json"
    event = {"params": {"url": "http://127.0.0.1:28800/v1/sessions?private=value"}}
    path.write_text(json.dumps({"events": [event]}), encoding="utf-8")
    assert summarize_network(path, (28800, 28900))["ok"]
    event["params"]["url"] = "http://127.0.0.1:18800/v1/sessions?private=value"
    path.write_text('{"events":[\n' + json.dumps(event) + ',\n{"partial":', encoding="utf-8")
    result = summarize_network(path, (28800, 28900))
    assert not result["ok"]
    assert not result["completeDocument"]
    assert result["loopbackUrlEventsByPort"] == {18800: 1}
    assert "private" not in json.dumps(result)


def fixture(tmp_path: Path):
    root = tmp_path / "install"
    payload = root / "packages/test"
    payload.mkdir(parents=True)
    (payload / "build-identity.json").write_text('{"buildId":"test"}', encoding="utf-8")
    (payload / "payload-manifest.json").write_text('{"files":[{"size":7}]}', encoding="utf-8")
    identity_hash = digest(payload / "build-identity.json")
    manifest_hash = digest(payload / "payload-manifest.json")
    receipt = {
        "payloadRelativePath": "packages/test",
        "buildId": "test",
        "profileId": "test",
        "payloadManifestSha256": manifest_hash,
        "buildIdentitySha256": identity_hash,
    }
    (root / "active-install.json").write_text(json.dumps(receipt), encoding="utf-8")
    helper = tmp_path / "helper"
    helper.write_bytes(b"fake executable never launched")
    success = {
        "ok": True,
        "buildIdentitySha256": identity_hash,
        "payloadManifestSha256": manifest_hash,
        "verification": {
            "totalMs": 2,
            "metadataMs": 0.2,
            "readMs": 0.8,
            "hashMs": 0.5,
            "manifestFiles": 1,
            "metadataChecked": 1,
            "filesVerified": 1,
            "bytesRead": 7,
        },
    }
    return root, helper, success


def test_verify_success_separates_first_sample_and_uses_only_verify(tmp_path, monkeypatch):
    root, helper, success = fixture(tmp_path)
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        assert args[1] == "verify"
        assert "--timings" in args
        assert kwargs["timeout"] == 5
        return subprocess.CompletedProcess(args, 0, json.dumps(success), "")

    monkeypatch.setattr("startup_verify.subprocess.run", invoke)
    report = {}
    measure(helper, root, 3, 5, report)
    assert report["ok"]
    assert len(calls) == 3
    assert report["firstSample"]["cacheState"] == "unknown"
    assert report["repeatSummary"]["sampleCount"] == 2
    assert all(row["timingsAvailable"] for row in report["samples"])


@pytest.mark.parametrize(
    "failure", ["identity", "partial", "invalid-metrics", "invalid-json", "exit", "timeout"]
)
def test_failed_samples_stop_and_are_not_counted_as_success(tmp_path, monkeypatch, failure):
    root, helper, success = fixture(tmp_path)
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if failure == "identity":
            success["buildIdentitySha256"] = "wrong"
        elif failure == "partial":
            success["verification"]["filesVerified"] = 0
        elif failure == "invalid-metrics":
            success["verification"]["totalMs"] = -1
        output = "not json private path" if failure == "invalid-json" else json.dumps(success)
        return subprocess.CompletedProcess(
            args, 32 if failure == "exit" else 0, output, "secret path"
        )

    monkeypatch.setattr("startup_verify.subprocess.run", invoke)
    report = {}
    measure(helper, root, 5, 5, report)
    assert not report["ok"]
    assert len(calls) == 1
    assert report["repeatSummary"]["successfulSamples"] == 0
    assert "secret path" not in json.dumps(report)
    assert "private path" not in json.dumps(report)


def test_old_helper_has_no_invented_internal_timings(tmp_path, monkeypatch):
    root, helper, success = fixture(tmp_path)
    del success["verification"]
    monkeypatch.setattr(
        "startup_verify.subprocess.run",
        lambda args, **_: subprocess.CompletedProcess(args, 0, json.dumps(success), ""),
    )
    report = {}
    measure(helper, root, 1, 5, report)
    assert report["ok"]
    assert not report["firstSample"]["timingsAvailable"]
    assert report["firstSample"]["verification"] is None


def test_incomplete_build_binding_is_rejected_before_launch(tmp_path, monkeypatch):
    root, helper, _ = fixture(tmp_path)
    binding = tmp_path / "build.json"
    binding.write_text(json.dumps({"ok": True, "helperSha256": digest(helper)}), encoding="utf-8")
    monkeypatch.setattr(
        "startup_verify.subprocess.run", lambda *_args, **_kwargs: pytest.fail("no launch")
    )
    with pytest.raises(ValueError, match="build report mismatch"):
        measure(helper, root, 1, 5, {}, binding)


def test_receipt_drift_invalidates_measurement(tmp_path, monkeypatch):
    root, helper, success = fixture(tmp_path)

    def invoke(args, **_):
        (root / "active-install.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, json.dumps(success), "")

    monkeypatch.setattr("startup_verify.subprocess.run", invoke)
    report = {}
    measure(helper, root, 1, 5, report)
    assert not report["inputsUnchanged"]
    assert not report["ok"]


def phase(phase_id, name, kind, start, elapsed, status="ok", session="target"):
    return {
        "processSessionId": session,
        "source": "startup",
        "kind": kind,
        "details": {
            "phase": name,
            "phaseId": phase_id,
            "startElapsedMs": start,
            "elapsedMs": elapsed,
            "durationMs": elapsed - start,
            "status": "running" if kind == "phase-started" else status,
            "context": {"private": "must-not-copy"},
        },
    }


def test_journal_keeps_nested_spans_and_marks_unpaired_and_duplicate(tmp_path):
    log = tmp_path / "log"
    rows = [
        phase(1, "tauri-build", "phase-started", 0, 0),
        phase(2, "tauri-setup", "phase-started", 5, 5),
        phase(2, "tauri-setup", "phase-finished", 5, 9),
        phase(1, "tauri-build", "phase-finished", 0, 10),
        phase(1, "tauri-build", "phase-finished", 0, 10),
        phase(3, "runtime-discovery", "phase-started", 10, 10),
        phase(4, "shell-context", "phase-finished", 0, 8, session="other"),
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    result = analyze([log], "target")
    assert len(result["phases"]) == 3
    assert result["phases"][0]["finish"]["durationMs"] == 10
    assert result["phases"][1]["finish"]["durationMs"] == 4
    assert "duplicate-phase-event" in result["gaps"]
    assert "unpaired-phase" in result["gaps"]
    assert not result["phaseCoverageComplete"]
    assert not result["firstInteractionVerified"]
    assert "must-not-copy" not in json.dumps(result)


def test_legacy_logs_report_missing_timing(tmp_path):
    log = tmp_path / "log"
    log.write_text(
        json.dumps(
            {
                "processSessionId": "legacy",
                "kind": "process-started",
                "details": {},
                "recordedAt": "2026-10-04T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    result = analyze([log], "legacy")
    assert result["ok"]
    assert result["phases"] == []
    assert result["missingPhases"]
    assert not result["phaseCoverageComplete"]


def test_report_preserves_runtime_identity_without_private_paths(tmp_path):
    log = tmp_path / "log"
    log.write_text(
        json.dumps(
            {
                "processSessionId": "target",
                "kind": "identity-resolved",
                "details": {
                    "executablePath": "private-path",
                    "runtimeKind": "production",
                    "buildIdentity": {
                        "buildId": "historical-build",
                        "dirty": True,
                        "sourceCommit": "historical-commit",
                        "private": "secret",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    result = analyze([log], "target")
    assert (
        result["observedRuntimeIdentities"][0]["buildIdentity"]["sourceCommit"]
        == "historical-commit"
    )
    assert result["runtimeSourceBinding"] == "from-log-not-runner-checkout"
    assert "private-path" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_output_collision_prevents_any_sampling(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("startup_profile_runner", SCRIPT_DIR / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    output = tmp_path / "evidence.json"
    output.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(runner, "measure", lambda *_: pytest.fail("must not start"))
    with pytest.raises(FileExistsError):
        runner.main(
            ["verify", "--helper", "unused", "--install-root", "unused", "--output", str(output)]
        )
    assert output.read_text(encoding="utf-8") == "keep"
