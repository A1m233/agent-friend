from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from install_contract import (  # noqa: E402
    DOWNGRADE_BLOCKED_ZH_CN,
    INSTALLER_SMOKE_PROFILE,
    INSTALLER_VERIFICATION_REQUIRED_FILES,
    PRODUCTION_PROFILE,
    WINDOWS_INSTALLER_MANUFACTURER,
    ActiveInstallReceipt,
    InstalledVersionState,
    InstallRootTransition,
    PackageVerificationLevel,
    PayloadFile,
    PayloadManifest,
    build_identity,
    build_installer_verification_inputs,
    canonical_json_bytes,
    classify_install_root_transition,
    classify_installed_version,
    classify_installer_impact,
    payload_install_relative_path,
    read_active_install_receipt,
    read_build_identity,
    resolve_install_profile,
    validate_payload_manifest,
    validate_profile_isolation,
    write_payload_manifest,
)


def _identity(*, profile_id: str = "production"):  # type: ignore[no-untyped-def]
    return build_identity(
        product_version="0.1.7",
        build_id="20260808T000000Z-abcdef123456-deadbeef",
        source={"commit": "a" * 40, "dirty": False, "tree_sha256": "b" * 64},
        local_test=True,
        trusted_beta=False,
        profile=resolve_install_profile(profile_id),
    )


def test_profiles_are_explicit_and_fully_isolated() -> None:
    validate_profile_isolation()
    assert PRODUCTION_PROFILE.profile_id == "production"
    assert WINDOWS_INSTALLER_MANUFACTURER == "agentfriend"
    assert INSTALLER_SMOKE_PROFILE.profile_id == "installer-smoke"
    assert PRODUCTION_PROFILE.instance_domain == "agent-friend-production"
    assert INSTALLER_SMOKE_PROFILE.instance_domain == "agent-friend-installer-smoke"
    assert PRODUCTION_PROFILE.to_json_dict()["keyringService"] == "agent-friend"
    assert (
        INSTALLER_SMOKE_PROFILE.to_json_dict()["keyringService"] == "agent-friend-installer-smoke"
    )


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="unsupported install profile"):
        resolve_install_profile("custom")


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        (None, InstalledVersionState.ABSENT),
        ("0.1.6", InstalledVersionState.UPGRADE),
        ("0.1.7", InstalledVersionState.REPAIR),
        ("0.1.8", InstalledVersionState.DOWNGRADE_BLOCKED),
    ],
)
def test_installed_version_state_forbids_downgrades(
    installed: str | None,
    expected: InstalledVersionState,
) -> None:
    assert classify_installed_version(installed, "0.1.7") == expected
    assert DOWNGRADE_BLOCKED_ZH_CN == ("当前已安装的 agent-friend 是更新的版本，此安装包无法继续")


def test_active_install_receipt_round_trips_and_rejects_noncanonical_input(tmp_path: Path) -> None:
    receipt = ActiveInstallReceipt(
        schema_version=1,
        profile_id="production",
        build_identity_sha256="a" * 64,
        build_id="20260808T000000Z-abcdef123456-deadbeef",
        payload_relative_path=("packages/20260808T000000Z-abcdef123456-deadbeef"),
        payload_manifest_sha256="b" * 64,
        activated_at="2026-08-08T00:00:00+00:00",
        transaction_id="transaction-1",
    )
    path = tmp_path / "active-install.json"
    path.write_text(json.dumps(receipt.to_json_dict()), encoding="utf-8")

    assert read_active_install_receipt(path) == receipt

    value = receipt.to_json_dict()
    value["unexpected"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not canonical"):
        read_active_install_receipt(path)


def test_install_root_transition_requires_explicit_move_intent() -> None:
    assert (
        classify_install_root_transition(None, r"C:\Apps\agent-friend", move_intent=False)
        == InstallRootTransition.NEW_INSTALL
    )
    assert (
        classify_install_root_transition(
            "C:\\Apps\\agent-friend\\",
            r"c:/apps/agent-friend",
            move_intent=False,
        )
        == InstallRootTransition.IN_PLACE
    )
    with pytest.raises(RuntimeError, match="explicit_move"):
        classify_install_root_transition(
            r"D:\agent-friend",
            r"C:\Apps\agent-friend",
            move_intent=False,
        )
    assert (
        classify_install_root_transition(
            r"D:\agent-friend",
            r"C:\Apps\agent-friend",
            move_intent=True,
        )
        == InstallRootTransition.EXPLICIT_MOVE
    )


def test_build_identity_is_canonical_and_bound_to_profile() -> None:
    identity = _identity(profile_id="installer-smoke")
    payload = json.loads(canonical_json_bytes(identity.to_json_dict()))
    assert payload["profileId"] == "installer-smoke"
    assert payload["buildMode"] == "local-test"
    assert payload["sourceCommit"] == "a" * 40
    assert payload["sourceTreeSha256"] == "b" * 64


def test_build_identity_round_trips_from_disk(tmp_path: Path) -> None:
    identity = _identity(profile_id="installer-smoke")
    path = tmp_path / "build-identity.json"
    path.write_bytes(canonical_json_bytes(identity.to_json_dict()))

    assert read_build_identity(path) == identity


def test_payload_manifest_hashes_every_file_and_verifies_tamper(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "python"
    runtime.mkdir(parents=True)
    executable = runtime / "agent-friend-runtime.exe"
    executable.write_bytes(b"runtime")
    model = tmp_path / "runtime" / "models" / "model.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")

    manifest = write_payload_manifest(tmp_path, _identity())

    assert [item.path for item in manifest.files] == [
        "build-identity.json",
        "runtime/models/model.onnx",
        "runtime/python/agent-friend-runtime.exe",
    ]
    validate_payload_manifest(tmp_path, manifest)

    executable.write_bytes(b"runtimf")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        validate_payload_manifest(tmp_path, manifest)


def test_payload_manifest_rejects_case_folded_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "A.txt"
    path.write_text("a", encoding="utf-8")
    digest = hashlib.sha256(b"a").hexdigest()
    manifest = PayloadManifest(
        schema_version=1,
        build_identity_sha256="0" * 64,
        profile_id="production",
        files=(
            PayloadFile(path="A.txt", size=1, sha256=digest, capability="support"),
            PayloadFile(path="a.txt", size=1, sha256=digest, capability="support"),
        ),
    )

    with pytest.raises(RuntimeError, match="duplicate path"):
        validate_payload_manifest(tmp_path, manifest)


def test_payload_relative_path_is_build_scoped() -> None:
    assert payload_install_relative_path("build-123") == "packages/build-123"
    with pytest.raises(RuntimeError, match="invalid build id"):
        payload_install_relative_path("../escape")


def _verification_repository(tmp_path: Path) -> set[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"],
        cwd=tmp_path,
        check=True,
    )
    paths = set(INSTALLER_VERIFICATION_REQUIRED_FILES)
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    return paths


def test_package_impact_separates_package_trigger_from_verification_level() -> None:
    impact = classify_installer_impact(
        {
            "docs/requirements/077/example/progress.md",
            ".cursor/rules/example.mdc",
            "AGENTS.md",
            "scripts/package/test_package.py",
            "scripts/README.md",
        }
    )
    assert impact["classification"] == "standard"
    assert impact["minimumVerificationLevel"] == "standard"
    assert impact["packageRequired"] is False
    assert impact["installerSensitivePaths"] == []

    unknown = classify_installer_impact({"new_product_area/feature.py"})
    assert unknown["classification"] == "standard"
    assert unknown["packageRequired"] is False

    diagnostics = classify_installer_impact(
        {"frontend/src-tauri/src/webview_diagnostics/windows_display_power.rs"}
    )
    assert diagnostics["minimumVerificationLevel"] == "standard"
    assert diagnostics["packageRequired"] is False

    frozen = classify_installer_impact(
        {
            "agent/pyproject.toml",
            "agent/src/agent/prompts/default.txt",
            "agent/src/agent/session_context.py",
        }
    )
    assert frozen["minimumVerificationLevel"] == "package-sensitive"
    assert frozen["packageRequired"] is True
    assert frozen["packageSensitivePaths"] == [
        "agent/pyproject.toml",
        "agent/src/agent/prompts/default.txt",
    ]
    assert frozen["standardPaths"] == ["agent/src/agent/session_context.py"]

    installer = classify_installer_impact(
        {"scripts/package/windows/nsis-hooks.nsh", "frontend/src/app.tsx"}
    )
    assert installer["minimumVerificationLevel"] == PackageVerificationLevel.FULL_MATRIX
    assert installer["packageRequired"] is True
    assert installer["fullMatrixPaths"] == ["scripts/package/windows/nsis-hooks.nsh"]


def test_installer_verification_fingerprint_ignores_docs_but_binds_code_and_environment(
    tmp_path: Path,
) -> None:
    paths = _verification_repository(tmp_path)
    docs = tmp_path / "docs" / "requirements" / "077" / "progress.md"
    docs.parent.mkdir(parents=True)
    docs.write_text("before", encoding="utf-8")
    paths.add("docs/requirements/077/progress.md")
    environment = {"rustc": "rustc 1", "pnpm": "10"}

    baseline = build_installer_verification_inputs(
        tmp_path,
        repository_paths=paths,
        build_environment=environment,
    )
    docs.write_text("after", encoding="utf-8")
    docs_only = build_installer_verification_inputs(
        tmp_path,
        repository_paths=paths,
        build_environment=environment,
    )
    assert docs_only == baseline

    code = tmp_path / "scripts" / "package" / "run.py"
    code.write_text("changed", encoding="utf-8")
    code_changed = build_installer_verification_inputs(
        tmp_path,
        repository_paths=paths,
        build_environment=environment,
    )
    assert code_changed.package_input_sha256 != baseline.package_input_sha256

    environment_changed = build_installer_verification_inputs(
        tmp_path,
        repository_paths=paths,
        build_environment={"rustc": "rustc 2", "pnpm": "10"},
    )
    assert environment_changed.package_input_sha256 != code_changed.package_input_sha256


def test_installer_verification_fingerprint_normalizes_utf8_checkout_line_endings(
    tmp_path: Path,
) -> None:
    lf_root = tmp_path / "lf-worktree"
    crlf_root = tmp_path / "crlf-worktree"
    lf_paths = _verification_repository(lf_root)
    crlf_paths = _verification_repository(crlf_root)
    lf_code = lf_root / "scripts" / "package" / "run.py"
    crlf_code = crlf_root / "scripts" / "package" / "run.py"
    environment = {"rustc": "rustc 1", "pnpm": "10"}

    lf_code.write_bytes(b"first\nsecond\n")
    crlf_code.write_bytes(b"first\r\nsecond\r\n")
    lf = build_installer_verification_inputs(
        lf_root,
        repository_paths=lf_paths,
        build_environment=environment,
    )
    crlf = build_installer_verification_inputs(
        crlf_root,
        repository_paths=crlf_paths,
        build_environment=environment,
    )
    assert crlf == lf

    crlf_code.write_bytes(b"first\r\nchanged\r\n")
    changed = build_installer_verification_inputs(
        crlf_root,
        repository_paths=crlf_paths,
        build_environment=environment,
    )
    assert changed.package_input_sha256 != lf.package_input_sha256


def test_installer_verification_fingerprint_keeps_binary_line_endings_exact(
    tmp_path: Path,
) -> None:
    paths = _verification_repository(tmp_path)
    code = tmp_path / "scripts" / "package" / "run.py"
    environment = {"rustc": "rustc 1", "pnpm": "10"}

    code.write_bytes(b"\x00first\r\nsecond")
    crlf = build_installer_verification_inputs(
        tmp_path,
        repository_paths=paths,
        build_environment=environment,
    )
    code.write_bytes(b"\x00first\nsecond")
    lf = build_installer_verification_inputs(
        tmp_path,
        repository_paths=paths,
        build_environment=environment,
    )
    assert lf.package_input_sha256 != crlf.package_input_sha256
