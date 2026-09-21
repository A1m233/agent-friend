from __future__ import annotations

import hashlib
import json
from pathlib import Path

import installer_smoke
import pytest
from install_contract import InstallerVerificationInputs
from installer_smoke import (
    REQUIRED_SCENARIOS,
    verify_smoke_report,
    verify_smoke_report_for_reuse,
)


def _inputs(*, sha256: str = "a" * 64) -> InstallerVerificationInputs:
    return InstallerVerificationInputs(
        schema_version=1,
        scope="windows-installer-shared-behavior-v1",
        package_input_sha256=sha256,
        file_count=10,
        build_environment=(("rustc", "rustc 1"),),
    )


def _report(path: Path, installer: Path, *, failed: str | None = None) -> None:
    scenarios = [
        {
            "name": name,
            "result": "failed" if name == failed else "passed",
            "details": {},
        }
        for name in sorted(REQUIRED_SCENARIOS)
    ]
    path.write_text(
        json.dumps(
            {
                "schemaVersion": installer_smoke.SMOKE_REPORT_SCHEMA,
                "kind": "installer-smoke",
                "profileId": "installer-smoke",
                "verificationInputs": _inputs().to_json_dict(),
                "productionCollisionPreflight": "passed",
                "candidate": {
                    "buildId": "candidate-build",
                    "installerRelative": "candidate/candidate.exe",
                    "installerSha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                },
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )


def test_verify_smoke_report_binds_build_id_hash_and_required_scenarios(
    tmp_path: Path,
) -> None:
    installer = tmp_path / "candidate.exe"
    installer.write_bytes(b"candidate")
    report = tmp_path / "report.json"
    _report(report, installer)

    verified = verify_smoke_report(
        report,
        candidate_build_id="candidate-build",
        candidate_installer=installer,
        expected_verification_inputs=_inputs(),
    )

    assert verified["productionCollisionPreflight"] == "passed"


def test_verify_smoke_report_rejects_failed_or_stale_evidence(tmp_path: Path) -> None:
    installer = tmp_path / "candidate.exe"
    installer.write_bytes(b"candidate")
    report = tmp_path / "report.json"
    _report(report, installer, failed="running_shell_upgrade")

    with pytest.raises(RuntimeError, match="not passing"):
        verify_smoke_report(
            report,
            candidate_build_id="candidate-build",
            candidate_installer=installer,
            expected_verification_inputs=_inputs(),
        )

    installer.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        verify_smoke_report(
            report,
            candidate_build_id="candidate-build",
            candidate_installer=installer,
            expected_verification_inputs=_inputs(),
        )

    installer.write_bytes(b"candidate")
    with pytest.raises(RuntimeError, match="fingerprint does not match"):
        verify_smoke_report(
            report,
            candidate_build_id="candidate-build",
            candidate_installer=installer,
            expected_verification_inputs=_inputs(sha256="b" * 64),
        )


def test_run_smoke_cleans_isolated_state_on_keyboard_interrupt(monkeypatch) -> None:
    cleaned: list[bool] = []
    monkeypatch.setattr(installer_smoke, "verify_smoke_environment", lambda: None)
    monkeypatch.setattr(
        installer_smoke,
        "_run_smoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(installer_smoke, "cleanup_smoke_state", lambda: cleaned.append(True))

    with pytest.raises(KeyboardInterrupt):
        installer_smoke.run_smoke(
            object(),
            build_report_path=Path("unused.json"),
            installed_smoke=None,
            expected_verification_inputs=_inputs(),
        )

    assert cleaned == [True]


def test_reuse_requires_explicit_report_path_and_matching_candidate_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    releases = tmp_path / "releases"
    installer = releases / "candidate" / "candidate.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"candidate")
    report = tmp_path / "report.json"
    _report(report, installer)
    value = json.loads(report.read_text(encoding="utf-8"))
    value["candidate"]["installerRelative"] = "candidate/candidate.exe"
    report.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        installer_smoke,
        "validate_release_directory",
        lambda _directory, *, formal: {
            "installer_verification": _inputs().to_json_dict(),
        },
    )
    roots = installer_smoke.PackageRoots(
        repo_root=tmp_path / "repo",
        state_root=tmp_path / "state",
        cache_dir=tmp_path / "state" / "cache",
        work_root=tmp_path / "state" / "work",
        releases_root=releases,
        repo_id="repo",
        worktree_id="worktree",
    )

    verified = verify_smoke_report_for_reuse(
        report,
        roots=roots,
        expected_verification_inputs=_inputs(),
    )

    assert verified["candidate"]["buildId"] == "candidate-build"

    with pytest.raises(RuntimeError, match="candidate installer package input fingerprint"):
        verify_smoke_report_for_reuse(
            report,
            roots=roots,
            expected_verification_inputs=_inputs(sha256="b" * 64),
        )


def test_smoke_disk_preflight_fails_before_install_mutation(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (baseline / "runtime.bin").write_bytes(b"b" * 11)
    (candidate / "payload.bin").write_bytes(b"c" * 13)
    installer = tmp_path / "installer.exe"
    installer.write_bytes(b"i" * 17)
    required = 11 + 13 + 17 + installer_smoke.SMOKE_DISK_MARGIN_BYTES

    with pytest.raises(RuntimeError, match="before any install mutation"):
        installer_smoke._smoke_disk_preflight(
            baseline_runtime=baseline,
            candidate_payload=candidate,
            installer=installer,
            available_bytes=required - 1,
        )

    assert installer_smoke._smoke_disk_preflight(
        baseline_runtime=baseline,
        candidate_payload=candidate,
        installer=installer,
        available_bytes=required,
    ) == {"availableBytes": required, "requiredBytes": required}


def test_required_scenarios_cover_first_install_and_preinstall_diagnostics() -> None:
    assert "fresh_install" in REQUIRED_SCENARIOS
    assert "preinstall_launch_failure_diagnostics" in REQUIRED_SCENARIOS
    assert installer_smoke.SMOKE_REPORT_SCHEMA == 3


def test_installer_diagnostics_parser_preserves_repeated_phases_and_equals(
    tmp_path: Path,
) -> None:
    diagnostic = tmp_path / "installer.log"
    diagnostic.write_text(
        "schemaVersion=1\r\n"
        "phase=preinstall\r\n"
        'helperOutput={"error":"path=a"}\r\n'
        "phase=rollback\r\n",
        encoding="utf-16",
    )

    parsed = installer_smoke._read_installer_diagnostics(diagnostic)

    assert parsed["phase"] == ["preinstall", "rollback"]
    assert parsed["helperOutput"] == ['{"error":"path=a"}']


def test_environment_failure_never_enters_install_or_cleanup(monkeypatch) -> None:
    def blocked():
        raise RuntimeError("filesystem redirection")

    def unexpected(*args, **kwargs):
        pytest.fail("must not install or clean existing smoke state")

    monkeypatch.setattr(installer_smoke, "verify_smoke_environment", blocked)
    monkeypatch.setattr(installer_smoke, "_run_smoke", unexpected)
    monkeypatch.setattr(installer_smoke, "cleanup_smoke_state", unexpected)
    with pytest.raises(RuntimeError, match="filesystem redirection"):
        installer_smoke.run_smoke(
            object(),
            build_report_path=Path("unused"),
            installed_smoke=None,
            expected_verification_inputs=_inputs(),
        )


def test_shell_locations_do_not_follow_appdata_overrides(monkeypatch, tmp_path: Path) -> None:
    folders = {key: tmp_path / key for key in ("local", "roaming", "programs", "desktop")}
    monkeypatch.setattr(installer_smoke, "shell_paths", lambda: folders)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "wrong-local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "wrong-roaming"))
    actual = installer_smoke._windows_paths()
    assert actual["install"].parent == folders["local"]
    assert actual["programs"] == folders["programs"]
    assert actual["desktop"] == folders["desktop"]
    # The failure fixture must exceed the shell's root-path limit itself;
    # resource extraction now supports long nested payload paths.
    assert len(str(actual["long_move_install"])) > 260
    assert len(actual["long_move_parent"].name) < 255
