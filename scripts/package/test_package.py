from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import brotli
import pytest

from runtime_config import (
    ALL_CREDENTIAL_NAMES,
    OPTIONAL_CREDENTIAL_NAMES,
    REQUIRED_CREDENTIAL_NAMES,
    CredentialStatus,
    CredentialValue,
    decrypt_credential_seed,
    encrypt_credential_seed,
)

PACKAGE_DIR = Path(__file__).resolve().parent
XHS_BUILDER = PACKAGE_DIR.parent / "_lib" / "xiaohongshu_connector" / "build.py"
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_package_version_is_consistent() -> None:
    package = _load("package_run", PACKAGE_DIR / "run.py")
    tauri = json.loads(
        (PACKAGE_DIR.parent.parent / "frontend" / "src-tauri" / "tauri.conf.json").read_text(
            "utf-8"
        )
    )
    assert package._project_version() == tauri["version"]


@pytest.mark.parametrize(
    "step",
    [
        "all",
        "installer-smoke-build",
        "installer-smoke",
        "installer-smoke-verify",
    ],
)
def test_package_build_and_verification_require_explicit_guide_baseline(step: str) -> None:
    package = _load("package_run_guide_required", PACKAGE_DIR / "run.py")
    args = SimpleNamespace(
        step=step,
        local_test=step == "all",
        guide_repo=None,
        guide_commit=None,
    )

    with (
        pytest.raises(RuntimeError, match="package build/verification steps require"),
        package._package_guide_baseline(args),
    ):
        raise AssertionError("unreachable")


def test_non_build_command_rejects_guide_baseline_arguments(tmp_path) -> None:
    package = _load("package_run_guide_nonbuild", PACKAGE_DIR / "run.py")
    args = SimpleNamespace(
        step="version-status",
        local_test=False,
        guide_repo=tmp_path / "guide",
        guide_commit="a" * 40,
    )

    with (
        pytest.raises(RuntimeError, match="only valid for package build/verification steps"),
        package._package_guide_baseline(args),
    ):
        raise AssertionError("unreachable")


def test_packaging_environment_removes_credentials(monkeypatch) -> None:
    package = _load("package_run_env", PACKAGE_DIR / "run.py")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    monkeypatch.setenv("KIMI_API_KEY", "must-not-leak")
    monkeypatch.setenv("VOLC_SPEECH_ACCESS_TOKEN", "must-not-leak")
    monkeypatch.setenv("VOLC_RTC_APP_ID", "must-not-leak")
    monkeypatch.setenv("SOME_VENDOR_SECRET_KEY", "must-not-leak")
    monkeypatch.setenv("PATH", "kept")

    env = package._sanitized_env()

    assert "DEEPSEEK_API_KEY" not in env
    assert "KIMI_API_KEY" not in env
    assert "VOLC_SPEECH_ACCESS_TOKEN" not in env
    assert "VOLC_RTC_APP_ID" not in env
    assert "SOME_VENDOR_SECRET_KEY" not in env
    assert env["PATH"] == "kept"
    assert env["AGENT_FRIEND_CONFIG_MODE"] == "env-only"


def test_child_build_output_is_safe_for_non_utf8_windows_console() -> None:
    package = _load("package_run_console", PACKAGE_DIR / "run.py")
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk")

    package._write_console_text("build complete \u2713\n", stream=stream)

    assert raw.getvalue().decode("gbk").splitlines() == ["build complete ?"]


def test_tauri_package_explicitly_selects_the_desktop_shell_binary() -> None:
    cargo = tomllib.loads(
        (PACKAGE_DIR.parent.parent / "frontend" / "src-tauri" / "Cargo.toml").read_text("utf-8")
    )
    main_source = (
        PACKAGE_DIR.parent.parent / "frontend" / "src-tauri" / "src" / "main.rs"
    ).read_text("utf-8")

    assert cargo["package"]["default-run"] == "app"
    assert "agent-friend-executable-role:desktop-shell-v1" in main_source


def test_installer_smoke_build_reuses_only_a_complete_validated_baseline(
    tmp_path, monkeypatch
) -> None:
    package = _load("package_run_reused_smoke_baseline", PACKAGE_DIR / "run.py")
    roots = package.PackageRoots(
        repo_root=tmp_path / "repo",
        state_root=tmp_path / "state",
        cache_dir=tmp_path / "state" / "cache",
        work_root=tmp_path / "state" / "work" / "repo" / "worktree",
        releases_root=tmp_path / "releases",
        repo_id="repo",
        worktree_id="worktree",
    )
    baseline_id = "baseline-build"
    baseline = package.create_build_paths(roots, build_id=baseline_id)
    baseline.payload_stage.mkdir(parents=True)
    baseline.runtime_stage.mkdir(parents=True)
    baseline_shell = baseline.build_dir / "smoke-baseline-shell.exe"
    baseline_shell.write_bytes(package.DESKTOP_SHELL_ROLE_MARKER + b"-baseline-shell")
    identity = package.build_identity(
        product_version="1.2.3",
        build_id=baseline_id,
        source={"commit": "a" * 40, "dirty": True, "tree_sha256": "b" * 64},
        local_test=True,
        trusted_beta=False,
        profile=package.resolve_install_profile("installer-smoke"),
    )
    runtime = baseline.payload_stage / "runtime" / "agent-friend-runtime.exe"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"baseline-runtime")
    package.write_payload_manifest(baseline.payload_stage, identity)

    candidate_installer = tmp_path / "candidate.exe"
    candidate_installer.write_bytes(b"candidate")
    child_calls = 0

    def fake_child_build(_roots, *, offline):
        nonlocal child_calls
        child_calls += 1
        assert offline is True
        return "candidate-build", candidate_installer

    captured: dict[str, object] = {}

    def fake_write_report(_roots, **kwargs):
        captured.update(kwargs)
        return tmp_path / "build-report.json"

    monkeypatch.setattr(package, "_child_smoke_build", fake_child_build)
    monkeypatch.setattr(
        package,
        "read_project_version",
        lambda _root: type("Project", (), {"version": "1.2.3"})(),
    )
    monkeypatch.setattr(package, "write_installer_smoke_build_report", fake_write_report)

    report = package._build_installer_smoke_pair(
        roots,
        offline=True,
        baseline_build_id=baseline_id,
    )

    assert report == tmp_path / "build-report.json"
    assert child_calls == 1
    assert captured["baseline_build_id"] == baseline_id
    assert captured["candidate_build_id"] == "candidate-build"

    runtime.write_bytes(b"tampered-runtime")
    try:
        package._build_installer_smoke_pair(
            roots,
            offline=True,
            baseline_build_id=baseline_id,
        )
    except RuntimeError as exc:
        assert "payload file" in str(exc)
    else:
        raise AssertionError("a tampered installer-smoke baseline must be rejected")


def test_installer_smoke_build_uses_one_candidate_build_for_default_fixture(
    tmp_path, monkeypatch
) -> None:
    package = _load("package_run_single_smoke_candidate", PACKAGE_DIR / "run.py")
    roots = package.PackageRoots(
        repo_root=tmp_path / "repo",
        state_root=tmp_path / "state",
        cache_dir=tmp_path / "state" / "cache",
        work_root=tmp_path / "state" / "work" / "repo" / "worktree",
        releases_root=tmp_path / "releases",
        repo_id="repo",
        worktree_id="worktree",
    )
    candidate_id = "candidate-build"
    candidate_paths = package.create_build_paths(roots, build_id=candidate_id)
    shared_shell = (
        candidate_paths.cargo_target_dir
        / "release"
        / f"{package.resolve_install_profile('installer-smoke').main_binary_name}.exe"
    )
    shared_shell.parent.mkdir(parents=True)
    shared_shell.write_bytes(package.DESKTOP_SHELL_ROLE_MARKER + b"-candidate-shell")
    candidate_installer = tmp_path / "candidate.exe"
    candidate_installer.write_bytes(b"candidate")
    child_calls = 0

    def fake_child_build(_roots, *, offline):
        nonlocal child_calls
        child_calls += 1
        return candidate_id, candidate_installer

    captured: dict[str, object] = {}
    monkeypatch.setattr(package, "_child_smoke_build", fake_child_build)
    monkeypatch.setattr(
        package,
        "read_project_version",
        lambda _root: type("Project", (), {"version": "1.2.3"})(),
    )
    monkeypatch.setattr(
        package,
        "write_installer_smoke_build_report",
        lambda _roots, **kwargs: captured.update(kwargs) or tmp_path / "build-report.json",
    )

    report = package._build_installer_smoke_pair(roots, offline=True)

    assert report == tmp_path / "build-report.json"
    assert child_calls == 1
    assert captured["baseline_build_id"] == candidate_id
    assert captured["candidate_build_id"] == candidate_id
    assert (
        candidate_paths.build_dir / "smoke-baseline-shell.exe"
    ).read_bytes() == shared_shell.read_bytes()


def test_production_installer_gate_requires_explicit_matching_smoke_report(
    tmp_path, monkeypatch
) -> None:
    package = _load("package_run_smoke_reuse_gate", PACKAGE_DIR / "run.py")
    roots = package.PackageRoots(
        repo_root=tmp_path / "repo",
        state_root=tmp_path / "state",
        cache_dir=tmp_path / "state" / "cache",
        work_root=tmp_path / "state" / "work",
        releases_root=tmp_path / "releases",
        repo_id="repo",
        worktree_id="worktree",
    )
    inputs = package.InstallerVerificationInputs(
        schema_version=1,
        scope="windows-installer-shared-behavior-v1",
        package_input_sha256="d" * 64,
        file_count=10,
        build_environment=(("rustc", "rustc 1"),),
    )
    try:
        package._verify_installer_smoke_gate(
            roots,
            step="all",
            profile=package.resolve_install_profile("production"),
            effective_level=package.PackageVerificationLevel.FULL_MATRIX,
            smoke_report=None,
            verification_inputs=inputs,
        )
    except RuntimeError as exc:
        assert "require an explicit --smoke-report" in str(exc)
    else:
        raise AssertionError("full-matrix production build accepted no smoke report")

    assert (
        package._verify_installer_smoke_gate(
            roots,
            step="all",
            profile=package.resolve_install_profile("production"),
            effective_level=package.PackageVerificationLevel.STANDARD,
            smoke_report=None,
            verification_inputs=inputs,
        )
        is None
    )

    report = tmp_path / "smoke.json"
    expected = {"candidate": {"buildId": "candidate-build"}}
    monkeypatch.setattr(
        package,
        "verify_smoke_report_for_reuse",
        lambda path, *, roots, expected_verification_inputs: expected,
    )
    assert (
        package._verify_installer_smoke_gate(
            roots,
            step="all",
            profile=package.resolve_install_profile("production"),
            effective_level=package.PackageVerificationLevel.FULL_MATRIX,
            smoke_report=report,
            verification_inputs=inputs,
        )
        == expected
    )


def test_effective_package_verification_level_can_upgrade_but_not_downgrade(tmp_path) -> None:
    package = _load("package_run_verification_level", PACKAGE_DIR / "run.py")
    level_two = {
        "minimumVerificationLevel": package.PackageVerificationLevel.PACKAGE_SENSITIVE.value
    }

    assert (
        package._effective_package_verification_level(
            level_two,
            requested=package.PackageVerificationLevel.STANDARD.value,
            release=False,
            smoke_report=None,
        )
        == package.PackageVerificationLevel.PACKAGE_SENSITIVE
    )
    assert (
        package._effective_package_verification_level(
            level_two,
            requested=package.PackageVerificationLevel.FULL_MATRIX.value,
            release=False,
            smoke_report=None,
        )
        == package.PackageVerificationLevel.FULL_MATRIX
    )
    assert (
        package._effective_package_verification_level(
            {"minimumVerificationLevel": package.PackageVerificationLevel.STANDARD.value},
            requested=None,
            release=True,
            smoke_report=None,
        )
        == package.PackageVerificationLevel.FULL_MATRIX
    )
    assert (
        package._effective_package_verification_level(
            {"minimumVerificationLevel": package.PackageVerificationLevel.STANDARD.value},
            requested=None,
            release=False,
            smoke_report=tmp_path / "smoke.json",
        )
        == package.PackageVerificationLevel.FULL_MATRIX
    )


def test_frozen_smoke_uses_an_explicit_non_secret_provider_placeholder() -> None:
    source = (PACKAGE_DIR / "run.py").read_text("utf-8")
    assert '"DEEPSEEK_API_KEY"] = "package-smoke-not-a-real-credential"' in source


def test_frozen_runtime_dependencies_have_explicit_packaging_review() -> None:
    policy = _load("package_dependency_policy", PACKAGE_DIR / "dependency_policy.py")

    policy.validate_frozen_runtime_dependency_policy(PACKAGE_DIR.parent.parent)
    assert policy.REVIEWED_DIRECT_DEPENDENCIES["trafilatura"] == "project-hook"
    assert (
        policy.REVIEWED_DIRECT_DEPENDENCIES["watchfiles"]
        == "agent-collect-all-module-graph-native-extension"
    )


def test_unreviewed_frozen_runtime_dependency_fails_closed(monkeypatch) -> None:
    policy = _load("package_dependency_policy_drift", PACKAGE_DIR / "dependency_policy.py")
    dependencies = {*policy.REVIEWED_DIRECT_DEPENDENCIES, "new-data-bearing-package"}
    monkeypatch.setattr(policy, "frozen_runtime_direct_dependencies", lambda _root: dependencies)

    try:
        policy.validate_frozen_runtime_dependency_policy(PACKAGE_DIR.parent.parent)
    except RuntimeError as exc:
        assert "unreviewed=new-data-bearing-package" in str(exc)
        assert "package data, hidden imports, native libraries" in str(exc)
    else:
        raise AssertionError("an unreviewed frozen-runtime dependency must fail closed")


def test_static_html_dependencies_use_project_hooks_for_package_data() -> None:
    spec_source = (PACKAGE_DIR / "windows" / "agent_friend_runtime.spec").read_text("utf-8")
    hooks_dir = PACKAGE_DIR / "windows" / "hooks"
    trafilatura_hook = (hooks_dir / "hook-trafilatura.py").read_text("utf-8")
    justext_hook = (hooks_dir / "hook-justext.py").read_text("utf-8")

    assert "hookspath=[str(hooks_dir)]" in spec_source
    assert 'collect_data_files("trafilatura")' in trafilatura_hook
    assert 'collect_data_files("justext")' in justext_hook


def test_installed_smoke_requires_complete_packaged_runtime(tmp_path) -> None:
    package = _load("package_run_installed_smoke", PACKAGE_DIR / "run.py")

    try:
        package._smoke_installed_runtime(tmp_path)
    except RuntimeError as exc:
        assert "installed runtime is incomplete" in str(exc)
        assert "active-install.json" in str(exc)
    else:
        raise AssertionError("incomplete installed runtime must fail closed")


def test_cloudflared_lock_has_pinned_version_and_hash() -> None:
    lock = json.loads((PACKAGE_DIR / "vendor" / "cloudflared.lock.json").read_text("utf-8"))
    assert lock["version"] != "latest"
    assert len(lock["sha256"]) == 64
    assert lock["url"].endswith("cloudflared-windows-amd64.exe")


def test_xiaohongshu_license_is_pinned_to_the_source_commit() -> None:
    package = _load("package_run_xiaohongshu_license", PACKAGE_DIR / "run.py")
    _, version, commit, filenames = package._windows_xiaohongshu_contract()

    assert version == "v2.2.7"
    assert commit == "3aacfb3583482424c0170e86e05725df940dd35e"
    assert filenames == (
        "xiaohongshu-mcp-windows-amd64.exe",
        "xiaohongshu-login-windows-amd64.exe",
    )
    assert (
        package.XIAOHONGSHU_LICENSE_SHA256
        == "9d8b162d4b4822b793666e2a8cb6c94df7ecdb8204f23a20e6042f93e691cb0d"
    )


def test_xiaohongshu_windows_build_lock_matches_runtime_manifest() -> None:
    builder = _load("xiaohongshu_windows_build_lock", XHS_BUILDER)
    from agent.tools.builtin.social_platforms.connectors.xiaohongshu_artifacts import (
        current_xiaohongshu_artifacts,
    )

    lock = builder.load_windows_build_lock()
    outputs = {item["role"]: item for item in lock["outputs"]}
    artifacts = current_xiaohongshu_artifacts(system="windows", machine="amd64")

    assert lock["inputs"]["xiaohongshu_mcp"]["commit"] == "3aacfb3583482424c0170e86e05725df940dd35e"
    assert lock["inputs"]["headless_browser"]["version"] == "v0.4.0"
    assert lock["inputs"]["go"]["version"] == "go1.26.5"
    for artifact in artifacts:
        output = outputs[artifact.role]
        assert artifact.source == "source-build"
        assert output["filename"] == artifact.filename
        assert output["size"] == artifact.size
        assert output["sha256"] == artifact.sha256


def test_xiaohongshu_windows_builder_uses_verified_offline_artifact_cache(tmp_path) -> None:
    builder = _load("xiaohongshu_windows_offline_cache", XHS_BUILDER)
    server = b"locked-server"
    login = b"locked-login"
    patch_sha = hashlib.sha256(builder.PATCH_PATH.read_bytes()).hexdigest()
    lock = {
        "schema_version": 1,
        "build_id": "test-build",
        "target": {"os": "windows", "arch": "amd64"},
        "inputs": {name: {} for name in ("go", "xiaohongshu_mcp", "headless_browser")},
        "patch": {"filename": builder.PATCH_PATH.name, "sha256": patch_sha},
        "outputs": [
            {
                "role": "server",
                "filename": "server.exe",
                "size": len(server),
                "sha256": hashlib.sha256(server).hexdigest(),
            },
            {
                "role": "login",
                "filename": "login.exe",
                "size": len(login),
                "sha256": hashlib.sha256(login).hexdigest(),
            },
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    cache = tmp_path / "cache"
    artifacts = cache / "artifacts" / "test-build"
    artifacts.mkdir(parents=True)
    (artifacts / "server.exe").write_bytes(server)
    (artifacts / "login.exe").write_bytes(login)

    paths = builder.ensure_windows_xiaohongshu_artifacts(
        tmp_path / "destination",
        cache_dir=cache,
        offline=True,
        lock_path=lock_path,
    )

    assert [path.read_bytes() for path in paths] == [server, login]


def test_xiaohongshu_windows_patch_isolated_from_ignored_parent_repo(tmp_path) -> None:
    builder = _load("xiaohongshu_windows_patch_isolation", XHS_BUILDER)
    parent = tmp_path / "parent"
    workspace = parent / "ignored" / "workspace"
    headless = workspace / "headless_browser" / "headless_browser.go"
    go_mod = workspace / "xiaohongshu-mcp" / "go.mod"
    search = workspace / "xiaohongshu-mcp" / "xiaohongshu" / "search.go"
    headless.parent.mkdir(parents=True)
    go_mod.parent.mkdir(parents=True)
    search.parent.mkdir(parents=True)
    headless.write_text(
        """func New(options ...Option) *Browser {
	for _, option := range options {
		option(cfg)
	}

	l := launcher.New().
		Headless(cfg.Headless).
		Set("--no-sandbox")

	return nil
}
""",
        encoding="utf-8",
    )
    go_mod.write_text(
        """module github.com/xpzouying/xiaohongshu-mcp

go 1.24.0

require (
	github.com/avast/retry-go/v4 v4.7.0
	github.com/gin-gonic/gin v1.10.1
	github.com/xpzouying/headless_browser v0.4.0
)
""",
        encoding="utf-8",
    )
    search.write_text(
        "\n" * 98
        + """func (s *SearchAction) Search() {
	page.MustNavigate(searchURL)
	page.MustWaitStable()
	page.MustWait(`() => window.__INITIAL_STATE__ !== undefined`)
}
""",
        encoding="utf-8",
    )
    parent.mkdir(exist_ok=True)
    (parent / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=parent, check=True)

    builder._apply_patch(workspace)

    assert "Leakless(false)" in headless.read_text(encoding="utf-8")
    assert "replace github.com/xpzouying/headless_browser" in go_mod.read_text(encoding="utf-8")
    patched_search = search.read_text(encoding="utf-8")
    assert "page.MustWaitStable()" not in patched_search
    assert "return Array.isArray(value);" in patched_search
    assert "search_results_wait_failed" in patched_search


def test_nsis_upgrade_preserves_and_normalizes_the_fixed_autostart_entry() -> None:
    tauri = json.loads(
        (PACKAGE_DIR.parent.parent / "frontend" / "src-tauri" / "tauri.conf.json").read_text(
            "utf-8"
        )
    )
    config = json.loads((PACKAGE_DIR / "windows" / "tauri.package.conf.json").read_text("utf-8"))
    nsis = config["bundle"]["windows"]["nsis"]
    assert tauri["mainBinaryName"] == "agent-friend"
    assert nsis["installMode"] == "currentUser"
    assert nsis["installerHooks"] == "../../scripts/package/windows/nsis-hooks.nsh"

    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")
    assert "MUI_CUSTOMFUNCTION_GUIINIT AgentFriendCaptureAutostart" in hooks
    assert "AgentFriendAutostartCaptured" in hooks
    assert "NSIS_HOOK_POSTINSTALL" in hooks
    assert '"${AGENT_FRIEND_AUTOSTART_VALUE}"' in hooks
    assert "$(AgentFriendIntegrityFailed)" in hooks


def test_nsis_uninstall_uses_tauri_cleanup_without_touching_private_startup_state() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")

    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert "DeleteRegValue" not in hooks
    assert "StartupApproved" not in hooks


def test_tauri_cli_is_pinned_for_the_verified_nsis_template() -> None:
    package = _load("package_run_nsis_overlay", PACKAGE_DIR / "run.py")
    package_json = json.loads(
        (PACKAGE_DIR.parent.parent / "frontend" / "package.json").read_text("utf-8")
    )
    assert package_json["devDependencies"]["@tauri-apps/cli"] == "2.11.2"
    assert package.TAURI_NSIS_TEMPLATE_SHA256 == (
        "ee84148e405adc4d736a46456dd8345a644751bd1f28a335dd7fd833a32d7c3e"
    )


def test_nsis_template_overlay_defaults_chinese_and_enforces_maintenance_state() -> None:
    package = _load("package_run_nsis_overlay_contract", PACKAGE_DIR / "run.py")
    source = (
        '{{#if installer_hooks}}\n!include "{{installer_hooks}}"\n{{/if}}\n'
        '  !if "${DISPLAYLANGUAGESELECTOR}" == "true"\n'
        "    !insertmacro MUI_LANGDLL_DISPLAY\n"
        "  !endif\n"
        '  nsis_tauri_utils::SemverCompare "${VERSION}" $R0\n'
        "  Pop $R0\n"
        '    StrCpy $R2 "$(addOrReinstall)"\n'
        '      MessageBox MB_ICONEXCLAMATION "$(unableToUninstall)"\n'
        "      Abort\n"
        "    ${EndIf}\n"
        "  reinst_done:\n"
        "!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n"
        "!insertmacro MUI_PAGE_DIRECTORY\n"
        "Section Install\n"
        "  SetOutPath $INSTDIR\n"
        "  !ifmacrodef NSIS_HOOK_PREUNINSTALL\n"
        "    !insertmacro NSIS_HOOK_PREUNINSTALL\n"
        "  !endif\n\n"
        '  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"\n'
    )
    identity = package.build_identity(
        product_version="1.2.3",
        build_id="build-123",
        source={"commit": "a" * 40, "dirty": False, "tree_sha256": "b" * 64},
        local_test=True,
        trusted_beta=False,
        profile=package.resolve_install_profile("installer-smoke"),
    )

    generated = package._apply_nsis_template_overlay(
        source,
        helper_path=PACKAGE_DIR / "helper.exe",
        identity=identity,
        profile=package.resolve_install_profile("installer-smoke"),
        trusted_resources=None,
    )

    assert "StrCpy $LANGUAGE ${LANG_SIMPCHINESE}" in generated
    assert "ReadRegStr $R9 HKCU" in generated
    assert "ReadRegDWORD $R9 HKCU" not in generated
    assert "!define MUI_LANGDLL_ALWAYSSHOW" in generated
    assert generated.index("!define MUI_LANGDLL_ALWAYSSHOW") < generated.index(
        "!insertmacro MUI_LANGDLL_DISPLAY"
    )
    assert "${AndIf} $WixMode != 1" in generated
    assert "AgentFriendDowngradeBlocked" in generated
    assert "AgentFriendRepairOrReinstall" in generated
    assert "AgentFriendConfirmInstallRoot" in generated
    assert "Same-version uninstall is a terminal maintenance action" in generated
    assert '!define AGENT_FRIEND_PROFILE_ID "installer-smoke"' in generated
    assert "!insertmacro AGENT_FRIEND_RESOLVE_INSTALL_ROOT_INTENT" in generated
    assert 'SetOutPath "$PLUGINSDIR"' in generated
    assert "File /oname=agent-friend-installer-helper.exe" in generated
    assert 'SetOutPath "$INSTDIR"' in generated
    assert '!define AGENT_FRIEND_PRODUCT_NAME "agent-friend-installer-smoke"' in generated
    assert '!define AGENT_FRIEND_PRODUCT_VERSION "1.2.3"' in generated
    assert "agent-friend requires its fail-closed uninstall guard" in generated


def test_nsis_install_uses_owned_blocker_helper_and_never_kills_by_name() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")

    assert "NSIS_HOOK_PREINSTALL" in hooks
    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert 'FindProcessCurrentUser "${executableName}"' in hooks
    assert "${AGENT_FRIEND_INSTALLER_HELPER}" in hooks
    assert "preinstall --install-root" in hooks
    assert "verify --install-root" in hooks
    assert "activate --install-root" in hooks
    assert "retire-source --install-root" in hooks
    assert "commit --install-root" in hooks
    assert "rollback --install-root" in hooks
    assert "--registered-root" in hooks
    assert "--move-intent" in hooks
    assert "!macroundef CheckIfAppIsRunning" in hooks
    assert "!macro CheckIfAppIsRunning executableName productName" in hooks
    assert "MUI_CUSTOMFUNCTION_ABORT AgentFriendUserAbort" in hooks
    assert "Function .onUserAbort" not in hooks
    assert "KillProcess" not in hooks
    assert "nsExec::ExecToStack" in hooks
    assert "AGENT_FRIEND_CAPTURE_HELPER_RESULT" in hooks


def test_nsis_preinstall_failure_is_first_install_safe_and_persists_diagnostics() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")

    assert "现有版本尚未被替换" not in hooks
    assert "尚未写入或替换程序文件" in hooks
    assert "$TEMP\\${AGENT_FRIEND_PRODUCT_NAME}-installer-${AGENT_FRIEND_BUILD_ID}.log" in hooks
    assert "FileWriteUTF16LE /BOM" in hooks
    assert "FileSeek $AgentFriendDiagnosticHandle 0 END" in hooks
    assert "installKind=$AgentFriendInstallKind" in hooks
    assert "schemaVersion=2" in hooks
    assert "product=${AGENT_FRIEND_PRODUCT_NAME}" in hooks
    assert "version=${AGENT_FRIEND_PRODUCT_VERSION}" in hooks
    assert "phase=${phase}" in hooks
    assert "helperResult=$AgentFriendHelperResult" in hooks
    assert "helperPath=$PLUGINSDIR\\${AGENT_FRIEND_INSTALLER_HELPER}" in hooks
    assert "helperPresentAfterRun=$AgentFriendHelperPresentAfterRun" in hooks
    assert "GetLastError" not in hooks
    assert "win32LastError" not in hooks
    assert "helperOutput=$AgentFriendHelperOutput" in hooks
    assert '!if "${AGENT_FRIEND_PROFILE_ID}" == "installer-smoke"' in hooks
    assert "/AGENTFRIENDSMOKEPREINSTALLLAUNCHFAIL" in hooks


def test_nsis_custom_messages_have_chinese_and_english_pairs() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")
    pairs: dict[str, set[str]] = {}
    for line in hooks.splitlines():
        if not line.startswith("LangString "):
            continue
        _, key, language, _text = line.split(" ", 3)
        pairs.setdefault(key, set()).add(language)

    assert pairs
    assert all(languages == {"2052", "1033"} for languages in pairs.values())
    assert (
        'LangString AgentFriendDowngradeBlocked 2052 "当前已安装的 agent-friend 是更新的版本，此安装包无法继续"'
        in hooks
    )


def test_runtime_entry_parser_requires_explicit_subcommand() -> None:
    runtime_entry = _load("runtime_entry", PACKAGE_DIR / "runtime_entry.py")
    assert runtime_entry._parse_args(["doctor"]).command == "doctor"
    bridge = runtime_entry._parse_args(["bridge", "--config-mode", "desktop"])
    assert bridge.command == "bridge"
    assert bridge.args == ["--config-mode", "desktop"]
    seed = runtime_entry._parse_args(
        ["seed-credentials", "--payload", "seed.bin", "--key-file", "seed.key"]
    )
    assert seed.command == "seed-credentials"
    assert seed.payload == Path("seed.bin")
    assert seed.key_file == Path("seed.key")
    status = runtime_entry._parse_args(["credential-status", "--require-operational"])
    assert status.command == "credential-status"
    assert status.require_operational is True


def test_doctor_without_configured_model_checks_static_html_extraction(monkeypatch, capsys) -> None:
    runtime_entry = _load("runtime_entry_without_model", PACKAGE_DIR / "runtime_entry.py")
    monkeypatch.delenv("AGENT_FRIEND_MEMORY_MODEL_DIR", raising=False)

    assert runtime_entry._doctor() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["capability:web_fetch_static_html"]["ok"] is True
    assert payload["checks"]["capability:tool_schema_validation"]["ok"] is True
    assert payload["checks"]["capability:scheduled_tasks"] == {
        "ok": True,
        "task_id": True,
        "scheduled_occurrence": 1,
        "interrupted_runs": 1,
        "history_outcome": "interrupted",
    }
    assert payload["checks"]["capability:governed_filesystem"]["ok"] is True
    assert payload["checks"]["capability:managed_execution_python"] == {
        "ok": True,
        "status": "not-configured",
        "source_mode": True,
    }
    skill_check = payload["checks"]["capability:skill_catalog_hot_reload"]
    assert skill_check["ok"] is True
    assert skill_check["builtin"] == "skill-creator"
    assert skill_check["installer"] == "skill-installer"
    assert skill_check["installer_validation"] == "fail-closed"
    assert skill_check["installer_managed_transaction"] == "rollback-ok"
    assert skill_check["same_turn_update_activation"] == "reloaded-with-version-isolation"
    assert skill_check["watcher_status"] == "healthy"
    assert skill_check["activated"] == "frozen-watcher-smoke"


def test_doctor_fails_closed_when_static_html_extraction_is_broken(monkeypatch, capsys) -> None:
    from agent.tools.builtin.web_fetch import html as web_fetch_html

    runtime_entry = _load("runtime_entry_broken_html", PACKAGE_DIR / "runtime_entry.py")
    monkeypatch.delenv("AGENT_FRIEND_MEMORY_MODEL_DIR", raising=False)

    def fail_extract(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("missing packaged HTML extractor resource")

    monkeypatch.setattr(web_fetch_html, "extract_static_html", fail_extract)

    assert runtime_entry._doctor() == 1
    payload = json.loads(capsys.readouterr().out)
    check = payload["checks"]["capability:web_fetch_static_html"]
    assert check == {
        "ok": False,
        "error_type": "RuntimeError",
        "error": "missing packaged HTML extractor resource",
    }


def test_doctor_fails_closed_when_packaged_skill_runtime_is_broken(monkeypatch, capsys) -> None:
    runtime_entry = _load("runtime_entry_broken_skill", PACKAGE_DIR / "runtime_entry.py")
    monkeypatch.delenv("AGENT_FRIEND_MEMORY_MODEL_DIR", raising=False)

    def fail_skill_smoke() -> dict[str, object]:
        raise RuntimeError("missing packaged builtin Skill resource")

    monkeypatch.setattr(runtime_entry, "_smoke_skill_catalog", fail_skill_smoke)

    assert runtime_entry._doctor() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["capability:skill_catalog_hot_reload"] == {
        "ok": False,
        "error_type": "RuntimeError",
        "error": "missing packaged builtin Skill resource",
    }


def test_doctor_fails_closed_when_scheduled_task_runtime_is_broken(monkeypatch, capsys) -> None:
    runtime_entry = _load("runtime_entry_broken_scheduled_tasks", PACKAGE_DIR / "runtime_entry.py")
    monkeypatch.delenv("AGENT_FRIEND_MEMORY_MODEL_DIR", raising=False)

    def fail_scheduled_task_smoke() -> dict[str, object]:
        raise RuntimeError("missing packaged Scheduled Task module")

    monkeypatch.setattr(runtime_entry, "_smoke_scheduled_tasks", fail_scheduled_task_smoke)

    assert runtime_entry._doctor() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["capability:scheduled_tasks"] == {
        "ok": False,
        "error_type": "RuntimeError",
        "error": "missing packaged Scheduled Task module",
    }


class _FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> CredentialValue | None:
        value = self.values.get(name)
        return CredentialValue(name=name, value=value, source="fake") if value else None

    def status(self, name: str) -> CredentialStatus:
        return CredentialStatus(name=name, configured=name in self.values, source="fake")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def test_runtime_seed_command_imports_without_printing_values(tmp_path, capsys) -> None:
    runtime_entry = _load("runtime_entry_seed", PACKAGE_DIR / "runtime_entry.py")
    credentials = {
        name: f"runtime-seed-never-print-{index}" for index, name in enumerate(ALL_CREDENTIAL_NAMES)
    }
    artifacts = encrypt_credential_seed(credentials)
    payload = tmp_path / "seed.bin"
    key = tmp_path / "seed.key"
    payload.write_bytes(artifacts.payload)
    key.write_bytes(artifacts.key)
    store = _FakeCredentialStore()

    assert runtime_entry._seed_credentials(payload, key, store=store) == 0
    assert store.values == credentials
    output = capsys.readouterr().out
    assert f'"installed": {len(ALL_CREDENTIAL_NAMES)}' in output
    for value in credentials.values():
        assert value not in output


def test_runtime_credential_status_allows_missing_optional_without_values(capsys) -> None:
    runtime_entry = _load("runtime_entry_status", PACKAGE_DIR / "runtime_entry.py")
    store = _FakeCredentialStore()
    for index, name in enumerate(REQUIRED_CREDENTIAL_NAMES):
        store.values[name] = f"status-never-print-{index}"

    assert runtime_entry._credential_status(require_operational=True, store=store) == 0
    output = capsys.readouterr().out
    for name in OPTIONAL_CREDENTIAL_NAMES:
        assert name in output
    assert "status-never-print" not in output


def test_runtime_credential_status_rejects_missing_required(capsys) -> None:
    runtime_entry = _load("runtime_entry_status_required", PACKAGE_DIR / "runtime_entry.py")
    store = _FakeCredentialStore()
    for index, name in enumerate(REQUIRED_CREDENTIAL_NAMES[1:]):
        store.values[name] = f"required-never-print-{index}"

    assert runtime_entry._credential_status(require_operational=True, store=store) == 2
    output = capsys.readouterr().out
    assert REQUIRED_CREDENTIAL_NAMES[0] in output
    assert "required-never-print" not in output


def test_nsis_postinstall_imports_and_deletes_trusted_seed() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")

    assert "NSIS_HOOK_POSTINSTALL" in hooks
    assert "trusted-credential-seed.bin" in hooks
    assert "trusted-credential-seed.key" in hooks
    assert "seed-credentials --payload" in hooks
    assert "$PLUGINSDIR\\trusted-credential-seed.bin" in hooks
    assert 'Delete "$PLUGINSDIR\\trusted-credential-seed.bin"' in hooks
    assert 'Delete "$PLUGINSDIR\\trusted-credential-seed.key"' in hooks
    assert "SetErrorLevel 33" in hooks


def test_trusted_resources_use_keyring_and_never_write_plaintext(tmp_path, monkeypatch) -> None:
    package = _load("package_run_trusted_resources", PACKAGE_DIR / "run.py")
    base_config = tmp_path / "tauri.package.conf.json"
    base_config.write_text(
        json.dumps({"bundle": {"resources": {"package-runtime/runtime/": "runtime"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(package, "TAURI_CONFIG", base_config)
    repo = tmp_path / "repo"
    common_git = repo / ".git"
    home = tmp_path / "home"
    common_git.mkdir(parents=True)
    home.mkdir()
    roots = package.resolve_package_roots(
        repo,
        state_dir=tmp_path / "state",
        releases_dir=tmp_path / "releases",
        home=home,
        common_git_dir=common_git,
    )
    build_paths = package.create_build_paths(roots, build_id="trusted-test")
    build_paths.build_dir.mkdir(parents=True)
    store = _FakeCredentialStore()
    credentials = {
        name: f"trusted-package-never-print-{index}"
        for index, name in enumerate(REQUIRED_CREDENTIAL_NAMES)
    }
    store.values.update(credentials)
    identity = package.build_identity(
        product_version="1.2.3",
        build_id=build_paths.build_id,
        source={"commit": "a" * 40, "dirty": True, "tree_sha256": "b" * 64},
        local_test=True,
        trusted_beta=True,
        profile=package.resolve_install_profile("production"),
    )
    nsis_template = tmp_path / "installer.nsi"
    nsis_template.write_text("template", encoding="utf-8")
    package._prepare_trusted_resources(build_paths, credential_store=store)

    config_path = package._write_package_config(
        build_paths,
        identity=identity,
        profile=package.resolve_install_profile("production"),
        trusted_beta=True,
        nsis_template=nsis_template,
    )
    payload, key = package._trusted_resource_paths(build_paths)
    assert payload.is_file()
    assert key.is_file()
    config = json.loads(config_path.read_text("utf-8"))
    resources = config["bundle"]["resources"]
    assert payload.as_posix() not in resources
    assert key.as_posix() not in resources
    assert resources[build_paths.payload_stage.as_posix() + "/"] == "packages/trusted-test"
    metadata_path = package._package_metadata_path(build_paths)
    assert resources[metadata_path.as_posix()] == "package-build.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    assert metadata["buildMode"] == "local-test"
    assert metadata["distribution"] == "trusted-beta"
    assert metadata["sensitive"] is True
    decoded = decrypt_credential_seed(payload.read_bytes(), key.read_bytes())
    for name in OPTIONAL_CREDENTIAL_NAMES:
        assert decoded[name] is None
    for value in credentials.values():
        encoded = value.encode()
        assert encoded not in payload.read_bytes()
        assert encoded not in key.read_bytes()
        assert value not in config_path.read_text("utf-8")


def test_trusted_artifact_paths_are_visibly_sensitive() -> None:
    releases = _load("package_release_artifact_paths", PACKAGE_DIR / "releases.py")

    ordinary_dir = releases.formal_release_dir(Path("releases"), "1.2.3", "ordinary")
    trusted_dir = releases.formal_release_dir(Path("releases"), "1.2.3", "trusted-beta")
    ordinary_name = releases.installer_name("1.2.3", distribution="ordinary", local_test=False)
    trusted_name = releases.installer_name("1.2.3", distribution="trusted-beta", local_test=False)

    assert ordinary_dir.name == "ordinary"
    assert ordinary_name == "agent-friend_1.2.3_windows_x64-setup.exe"
    assert "trusted-beta-sensitive" in trusted_dir.name
    assert "trusted-beta-SENSITIVE" in trusted_name

    local_ordinary = releases.local_test_release_dir(
        Path("releases"), "1.2.3", "ordinary", "build-one"
    )
    local_trusted = releases.local_test_release_dir(
        Path("releases"), "1.2.3", "trusted-beta", "build-two"
    )
    assert "ordinary" in local_ordinary.parts
    assert "trusted-beta-sensitive" in local_trusted.parts


def test_package_roots_are_stable_across_worktrees_and_builds_are_isolated(tmp_path) -> None:
    package_paths = _load("package_paths_isolation", PACKAGE_DIR / "paths.py")
    common_git = tmp_path / "main" / ".git"
    first_repo = tmp_path / "worktrees" / "first"
    second_repo = tmp_path / "worktrees" / "second"
    state = tmp_path / "machine-state"
    releases = tmp_path / "machine-releases"
    home = tmp_path / "home"
    for path in (common_git, first_repo, second_repo, home):
        path.mkdir(parents=True)

    first = package_paths.resolve_package_roots(
        first_repo,
        state_dir=state,
        releases_dir=releases,
        home=home,
        common_git_dir=common_git,
    )
    second = package_paths.resolve_package_roots(
        second_repo,
        state_dir=state,
        releases_dir=releases,
        home=home,
        common_git_dir=common_git,
    )

    assert first.releases_root == second.releases_root == releases.resolve()
    assert first.cache_dir == second.cache_dir
    assert first.repo_id == second.repo_id
    assert first.worktree_id != second.worktree_id
    assert first.work_root != second.work_root
    build_one = package_paths.create_build_paths(first, build_id="build-one")
    build_two = package_paths.create_build_paths(first, build_id="build-two")
    assert build_one.build_dir != build_two.build_dir
    assert build_one.cargo_target_dir == build_two.cargo_target_dir
    assert build_one.pyinstaller_cache_dir == build_two.pyinstaller_cache_dir


def test_package_roots_reject_repository_and_broad_user_paths(tmp_path) -> None:
    package_paths = _load("package_paths_safety", PACKAGE_DIR / "paths.py")
    repo = tmp_path / "repo"
    common_git = repo / ".git"
    home = tmp_path / "home"
    for path in (common_git, home):
        path.mkdir(parents=True)

    for state, releases in (
        (repo / "package-output", tmp_path / "releases"),
        (tmp_path / "state", home),
    ):
        try:
            package_paths.resolve_package_roots(
                repo,
                state_dir=state,
                releases_dir=releases,
                home=home,
                common_git_dir=common_git,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("unsafe package roots must fail closed")


def test_safe_rmtree_only_deletes_inside_current_build(tmp_path) -> None:
    package_paths = _load("package_paths_delete", PACKAGE_DIR / "paths.py")
    repo = tmp_path / "repo"
    common_git = repo / ".git"
    home = tmp_path / "home"
    common_git.mkdir(parents=True)
    home.mkdir()
    roots = package_paths.resolve_package_roots(
        repo,
        state_dir=tmp_path / "state",
        releases_dir=tmp_path / "releases",
        home=home,
        common_git_dir=common_git,
    )
    build = package_paths.create_build_paths(roots, build_id="safe-build")
    child = build.build_dir / "child"
    child.mkdir(parents=True)

    package_paths.safe_rmtree(child, build_paths=build)
    assert not child.exists()
    try:
        package_paths.safe_rmtree(roots.state_root, build_paths=build)
    except RuntimeError:
        pass
    else:
        raise AssertionError("state root deletion must be rejected")

    cached_child = build.cargo_target_dir / "release" / "bundle" / "nsis"
    cached_child.mkdir(parents=True)
    package_paths.safe_cache_rmtree(cached_child, build_paths=build)
    assert not cached_child.exists()


def _write_version_fixture(root: Path, version: str = "1.2.3") -> None:
    tauri = root / "frontend" / "src-tauri"
    tauri.mkdir(parents=True)
    (tauri / "tauri.conf.json").write_text(
        json.dumps({"productName": "agent-friend", "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "frontend" / "package.json").write_text(
        json.dumps({"name": "frontend", "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    (tauri / "Cargo.toml").write_text(
        f'[package]\nname = "app"\nversion = "{version}"\n\n[dependencies]\n',
        encoding="utf-8",
    )
    (tauri / "Cargo.lock").write_text(
        f'version = 4\n\n[[package]]\nname = "app"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def test_semver_validation_and_precedence() -> None:
    versioning = _load("package_versioning_semver", PACKAGE_DIR / "versioning.py")
    assert versioning.SemVer.parse("1.0.0-alpha.1") < versioning.SemVer.parse("1.0.0")
    assert versioning.SemVer.parse("1.0.0+first") == versioning.SemVer.parse("1.0.0+second")
    for invalid in ("1", "1.2", "01.2.3", "1.2.3-01", "v1.2.3"):
        try:
            versioning.SemVer.parse(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid SemVer was accepted: {invalid}")


def test_package_build_intent_requires_an_explicit_governed_channel() -> None:
    package = _load("package_run_build_intent", PACKAGE_DIR / "run.py")
    production = package.resolve_install_profile("production")
    installer_smoke = package.resolve_install_profile("installer-smoke")

    for intent in (
        {"local_test": True, "trusted_beta": False, "release": False},
        {"local_test": False, "trusted_beta": True, "release": False},
        {"local_test": False, "trusted_beta": False, "release": True},
    ):
        package._validate_package_build_intent(
            step="all",
            profile=production,
            **intent,
        )
    package._validate_package_build_intent(
        step="all",
        profile=installer_smoke,
        local_test=True,
        trusted_beta=False,
        release=False,
    )

    invalid = (
        (
            {"step": "all", "profile": production},
            {"local_test": False, "trusted_beta": False, "release": False},
            "package intent is required",
        ),
        (
            {"step": "all", "profile": production},
            {"local_test": True, "trusted_beta": False, "release": True},
            "cannot be combined",
        ),
        (
            {"step": "all", "profile": installer_smoke},
            {"local_test": False, "trusted_beta": False, "release": False},
            "requires --local-test",
        ),
        (
            {"step": "all", "profile": installer_smoke},
            {"local_test": True, "trusted_beta": False, "release": True},
            "cannot be combined",
        ),
        (
            {"step": "runtime", "profile": production},
            {"local_test": False, "trusted_beta": True, "release": False},
            "requires the installer or all step",
        ),
    )
    for context, intent, message in invalid:
        try:
            package._validate_package_build_intent(**context, **intent)
        except RuntimeError as error:
            assert message in str(error)
        else:
            raise AssertionError(f"invalid package intent was accepted: {context} {intent}")


def test_local_test_trusted_beta_uses_the_governed_next_beta(monkeypatch, tmp_path) -> None:
    package = _load("package_run_local_trusted_beta_version", PACKAGE_DIR / "run.py")
    project = package.ProjectVersion(
        version=package.SemVer.parse("0.2.0"),
        values={"tauri": "0.2.0"},
    )
    target = package.SemVer.parse("0.2.1")
    candidate = package.SemVer.parse("0.2.1-beta.20260812.1")
    monkeypatch.setattr(package, "discover_formal_releases", lambda _root: ([], []))
    monkeypatch.setattr(
        package,
        "_trusted_beta_candidate",
        lambda _roots, _project, _records: (target, candidate),
    )
    roots = package.PackageRoots(
        repo_root=tmp_path,
        state_root=tmp_path / "state",
        cache_dir=tmp_path / "state" / "cache",
        work_root=tmp_path / "state" / "work",
        releases_root=tmp_path / "releases",
        repo_id="repo",
        worktree_id="worktree",
    )

    assert package._planned_package_version(
        roots,
        project,
        local_test=True,
        trusted_beta=True,
    ) == (target, candidate)


def test_production_install_plan_blocks_downgrade_before_build(monkeypatch) -> None:
    package = _load("package_run_production_install_plan", PACKAGE_DIR / "run.py")
    monkeypatch.setattr(
        package,
        "_read_current_production_install",
        lambda: {"version": "0.2.1-beta.20260811.1", "buildId": "installed-build"},
    )

    blocked = package._production_install_plan(package.SemVer.parse("0.2.0"))
    assert blocked["state"] == "downgrade-blocked"
    assert blocked["allowed"] is False
    with pytest.raises(RuntimeError, match="rejected a downgrade before build"):
        package._require_install_candidate_allowed(blocked)

    upgrade = package._production_install_plan(package.SemVer.parse("0.2.1-beta.20260812.1"))
    assert upgrade["state"] == "upgrade"
    assert upgrade["allowed"] is True
    package._require_install_candidate_allowed(upgrade)


def test_trusted_beta_channel_allocates_monotonic_daily_prerelease_versions(tmp_path) -> None:
    versioning = _load("package_versioning_trusted_beta", PACKAGE_DIR / "versioning.py")
    channel = versioning.configure_trusted_beta_channel(
        tmp_path,
        "1.3.0",
        minimum_release_version=versioning.SemVer.parse("1.2.3"),
    )

    first = versioning.next_trusted_beta_version(
        channel,
        [],
        now=datetime(2026, 8, 8, tzinfo=UTC),
    )
    third = versioning.next_trusted_beta_version(
        channel,
        [first, versioning.SemVer.parse("1.3.0-beta.20260808.2")],
        now=datetime(2026, 8, 8, tzinfo=UTC),
    )
    next_day = versioning.next_trusted_beta_version(
        channel,
        [third],
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert str(first) == "1.3.0-beta.20260808.1"
    assert str(third) == "1.3.0-beta.20260808.3"
    assert str(next_day) == "1.3.0-beta.20260809.1"
    assert first > versioning.SemVer.parse("1.2.3")
    assert next_day < versioning.SemVer.parse("1.3.0")
    assert versioning.read_trusted_beta_channel(tmp_path) == channel


def test_trusted_beta_channel_rejects_unstable_or_non_increasing_targets(tmp_path) -> None:
    versioning = _load("package_versioning_trusted_beta_guard", PACKAGE_DIR / "versioning.py")
    minimum = versioning.SemVer.parse("1.2.3")

    for value in ("1.2.3", "1.3.0-beta.1", "1.3.0+build"):
        try:
            versioning.configure_trusted_beta_channel(
                tmp_path,
                value,
                minimum_release_version=minimum,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"invalid trusted-beta target was accepted: {value}")


def test_trusted_beta_channel_fails_closed_on_clock_rollback(tmp_path) -> None:
    versioning = _load("package_versioning_trusted_beta_clock", PACKAGE_DIR / "versioning.py")
    channel = versioning.configure_trusted_beta_channel(
        tmp_path,
        "1.3.0",
        minimum_release_version=versioning.SemVer.parse("1.2.3"),
    )

    try:
        versioning.next_trusted_beta_version(
            channel,
            [versioning.SemVer.parse("1.3.0-beta.20260809.1")],
            now=datetime(2026, 8, 8, tzinfo=UTC),
        )
    except RuntimeError as error:
        assert "system UTC date" in str(error)
    else:
        raise AssertionError("clock rollback produced a non-monotonic trusted-beta version")


def test_project_version_set_updates_all_product_metadata(tmp_path) -> None:
    versioning = _load("package_versioning_update", PACKAGE_DIR / "versioning.py")
    _write_version_fixture(tmp_path)

    result = versioning.set_project_version(tmp_path, "1.3.0-beta.1")

    assert str(result.version) == "1.3.0-beta.1"
    assert set(result.values.values()) == {"1.3.0-beta.1"}
    assert 'version = "1.3.0-beta.1"' in (
        tmp_path / "frontend" / "src-tauri" / "Cargo.lock"
    ).read_text(encoding="utf-8")


def test_project_version_set_rolls_back_after_partial_replace(tmp_path) -> None:
    versioning = _load("package_versioning_rollback", PACKAGE_DIR / "versioning.py")
    _write_version_fixture(tmp_path)
    calls = 0

    def fail_second(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replace failure")
        os.replace(source, target)

    try:
        versioning.set_project_version(tmp_path, "2.0.0", replace=fail_second)
    except OSError:
        pass
    else:
        raise AssertionError("partial replace failure must be reported")
    restored = versioning.read_project_version(tmp_path)
    assert str(restored.version) == "1.2.3"


def _release_manifest(*, version: str, build_id: str, local_test: bool) -> dict[str, object]:
    return {
        "schema_version": 2,
        "product": "agent-friend",
        "version": version,
        "platform": "windows-x64",
        "build_mode": "local-test" if local_test else "formal",
        "distribution": "ordinary",
        "publishable": not local_test,
        "sensitive": False,
        "created_at": datetime.now(UTC).isoformat(),
        "build_id": build_id,
        "source": {"commit": "a" * 40, "dirty": local_test},
        "toolchain": {"python": "3.12", "tauri_cli": "2.11.2"},
        "installer": {},
        "upgrade_mode": "manual-installer",
        "identifier": "com.agentfriend.desktop",
    }


def test_formal_releases_are_immutable_and_local_test_is_not_latest(tmp_path) -> None:
    releases = _load("package_releases_publish", PACKAGE_DIR / "releases.py")
    candidate = tmp_path / "candidate.exe"
    candidate.write_bytes(b"installer")
    root = tmp_path / "release-root"
    formal_manifest = _release_manifest(version="1.2.3", build_id="formal-one", local_test=False)
    setup = releases.publish_installer(
        candidate,
        releases_root=root,
        manifest=formal_manifest,
        local_test=False,
    )
    assert setup.is_file()
    try:
        releases.publish_installer(
            candidate,
            releases_root=root,
            manifest=formal_manifest,
            local_test=False,
        )
    except RuntimeError as exc:
        assert "immutable" in str(exc)
    else:
        raise AssertionError("formal release overwrite must fail closed")

    local_manifest = _release_manifest(version="9.0.0", build_id="local-one", local_test=True)
    local_setup = releases.publish_installer(
        candidate,
        releases_root=root,
        manifest=local_manifest,
        local_test=True,
    )
    assert "local-test-NOT-FOR-DISTRIBUTION" in local_setup.name
    records, warnings = releases.discover_formal_releases(root)
    assert not warnings
    assert str(releases.latest_formal_version(records)) == "1.2.3"


def test_invalid_formal_manifest_is_ignored_with_warning(tmp_path) -> None:
    releases = _load("package_releases_invalid", PACKAGE_DIR / "releases.py")
    invalid = tmp_path / "releases" / "releases" / "2.0.0" / "windows-x64" / "ordinary"
    invalid.mkdir(parents=True)
    (invalid / "release.json").write_text("{}", encoding="utf-8")

    records, warnings = releases.discover_formal_releases(tmp_path / "releases")

    assert not records
    assert len(warnings) == 1


def test_installer_build_uses_external_cargo_target_and_generated_resource_config(
    tmp_path, monkeypatch
) -> None:
    package = _load("package_run_external_build", PACKAGE_DIR / "run.py")
    guide_commit = "d" * 40
    guide_snapshot = "e" * 64
    monkeypatch.setattr(
        package,
        "_ACTIVE_GUIDE_BASELINE",
        package.GuideBaseline(
            guide_commit=guide_commit,
            snapshot_sha256=guide_snapshot,
            guide_repo=tmp_path / "guide",
            lifecycle="transient",
        ),
    )
    repo = tmp_path / "repo"
    common_git = repo / ".git"
    home = tmp_path / "home"
    common_git.mkdir(parents=True)
    home.mkdir()
    roots = package.resolve_package_roots(
        repo,
        state_dir=tmp_path / "state",
        releases_dir=tmp_path / "releases",
        home=home,
        common_git_dir=common_git,
    )
    build_paths = package.create_build_paths(roots, build_id="external-build")
    build_paths.payload_stage.mkdir(parents=True)
    (build_paths.payload_stage / "payload-manifest.json").write_text("{}", encoding="utf-8")
    build_paths.cargo_target_dir.mkdir(parents=True)
    stale_marker = build_paths.cargo_target_dir / "stale-frontend-embedding"
    stale_marker.write_text("must be removed", encoding="utf-8")
    base_config = tmp_path / "tauri.package.conf.json"
    base_config.write_text(
        json.dumps(
            {
                "bundle": {
                    "resources": {
                        "package-runtime/runtime/": "runtime",
                        "../avatar/": "avatar-seed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(package, "TAURI_CONFIG", base_config)
    monkeypatch.setattr(package.shutil, "which", lambda _: "pnpm")
    monkeypatch.setattr(package, "_toolchain_versions", lambda _: {"tauri_cli": "2.11.2"})
    generated_template = tmp_path / "installer.generated.nsi"
    generated_template.write_text("template", encoding="utf-8")
    helper = tmp_path / "agent-friend-installer-helper.exe"
    helper.write_bytes(b"helper")
    monkeypatch.setattr(package, "_build_installer_helper", lambda _paths, *, env: helper)
    monkeypatch.setattr(
        package,
        "_generate_nsis_template",
        lambda _build_paths, **_kwargs: generated_template,
    )
    published: dict[str, object] = {}

    def fake_run(command, *, cwd=package.REPO_ROOT, env=None) -> None:
        assert env is not None
        if command[1:3] == ["run", "build"]:
            assert env["AGENT_FRIEND_PRODUCT_VERSION"] == "1.2.3"
            assert env["CARGO_TARGET_DIR"] == str(build_paths.cargo_target_dir)
            assert command[3] == "--outDir"
            build_paths.frontend_dist.mkdir(parents=True)
            for entrypoint in package.REQUIRED_FRONTEND_ENTRYPOINTS:
                (build_paths.frontend_dist / entrypoint).write_text("ok", encoding="utf-8")
            return
        if command[0:2] == ["cargo", "clean"]:
            assert "--package" in command
            assert "app" in command
            assert stale_marker.exists()
            return
        assert env["AGENT_FRIEND_PRODUCT_VERSION"] == "1.2.3"
        assert stale_marker.exists()
        config_path = Path(command[command.index("--config") + 1])
        config = json.loads(config_path.read_text("utf-8"))
        assert config["version"] == "1.2.3"
        resources = config["bundle"]["resources"]
        assert "package-runtime/runtime/" not in resources
        payload_resource = build_paths.payload_stage.as_posix() + "/"
        assert resources[payload_resource] == "packages/external-build"
        assert config["build"]["beforeBuildCommand"] == ""
        assert config["build"]["frontendDist"] == package._tauri_relative_path(
            build_paths.frontend_dist
        )
        bundle = build_paths.cargo_target_dir / "release" / "bundle" / "nsis"
        bundle.mkdir(parents=True)
        (bundle / "candidate.exe").write_bytes(b"candidate")
        executable = build_paths.cargo_target_dir / "release" / "agent-friend.exe"
        codegen = (
            build_paths.cargo_target_dir
            / "release"
            / "build"
            / "app-fake"
            / "out"
            / "tauri-codegen-assets"
        )
        codegen.mkdir(parents=True)
        for index, path in enumerate(sorted(build_paths.frontend_dist.rglob("*"))):
            if path.is_file():
                (codegen / f"{index:064x}{path.suffix}").write_bytes(
                    brotli.compress(path.read_bytes())
                )
        executable.write_bytes(package.DESKTOP_SHELL_ROLE_MARKER + b"-with-compressed-assets")

    def fake_publish(candidate, *, releases_root, manifest, local_test):
        published.update(
            candidate=candidate,
            releases_root=releases_root,
            manifest=manifest,
            local_test=local_test,
        )
        return tmp_path / "published.exe"

    monkeypatch.setattr(package, "_run", fake_run)
    monkeypatch.setattr(package, "publish_installer", fake_publish)

    identity = package.build_identity(
        product_version="1.2.3",
        build_id=build_paths.build_id,
        source={"commit": "a" * 40, "dirty": True, "tree_sha256": "b" * 64},
        local_test=True,
        trusted_beta=False,
        profile=package.resolve_install_profile("production"),
    )
    result = package._build_installer(
        "1.2.3",
        build_paths=build_paths,
        identity=identity,
        profile=package.resolve_install_profile("production"),
        verification_inputs=package.InstallerVerificationInputs(
            schema_version=1,
            scope="windows-installer-shared-behavior-v1",
            package_input_sha256="c" * 64,
            file_count=10,
            build_environment=(("rustc", "rustc 1"),),
        ),
        local_test=True,
    )

    assert result == tmp_path / "published.exe"
    assert published["local_test"] is True
    assert published["releases_root"] == roots.releases_root
    manifest = published["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["build_mode"] == "local-test"
    assert manifest["release_channel"] == "local-test"
    assert manifest["publishable"] is False
    assert manifest["installer_verification"]["packageInputSha256"] == "c" * 64
    assert manifest["source"]["guide_baseline"] == {
        "source_commit": guide_commit,
        "snapshot_sha256": guide_snapshot,
        "lifecycle": "transient",
    }
    metadata = json.loads(package._package_metadata_path(build_paths).read_text("utf-8"))
    assert metadata["distribution"] == "ordinary"


def test_frontend_dist_validation_rejects_missing_product_entrypoints(tmp_path) -> None:
    package = _load("package_run_frontend_dist_guard", PACKAGE_DIR / "run.py")
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("ok", encoding="utf-8")

    try:
        package._validate_frontend_dist(frontend_dist)
    except RuntimeError as error:
        assert "frontend build output is incomplete" in str(error)
        assert "chat.html" in str(error)
        assert "pet.html" in str(error)
    else:
        raise AssertionError("incomplete frontendDist must be rejected")


def test_embedded_frontend_validation_rejects_stale_tauri_executable(tmp_path) -> None:
    package = _load("package_run_embedded_frontend_guard", PACKAGE_DIR / "run.py")
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    for name in ("index.html", "chat.html", "pet.html"):
        (frontend_dist / name).write_text(name, encoding="utf-8")
    cargo_target = tmp_path / "target"
    codegen = cargo_target / "release" / "build" / "app-fake" / "out" / "tauri-codegen-assets"
    codegen.mkdir(parents=True)
    for index, name in enumerate(("index.html", "chat.html")):
        (codegen / f"{index:064x}.html").write_bytes(
            brotli.compress((frontend_dist / name).read_bytes())
        )
    executable = tmp_path / "agent-friend.exe"
    executable.write_bytes(package.DESKTOP_SHELL_ROLE_MARKER + b"-with-stale-assets")

    try:
        package._validate_embedded_frontend(
            executable,
            frontend_dist,
            ["index.html", "chat.html", "pet.html"],
            cargo_target,
        )
    except RuntimeError as error:
        assert "Brotli codegen assets" in str(error)
        assert "pet.html" in str(error)
    else:
        raise AssertionError("stale Tauri executable must be rejected")


def test_embedded_frontend_validation_rejects_a_helper_binary(tmp_path) -> None:
    package = _load("package_run_shell_role_guard", PACKAGE_DIR / "run.py")
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    entrypoints = ["index.html", "chat.html", "pet.html"]
    for name in entrypoints:
        (frontend_dist / name).write_text(name, encoding="utf-8")
    cargo_target = tmp_path / "target"
    codegen = cargo_target / "release" / "build" / "app-fake" / "out" / "tauri-codegen-assets"
    codegen.mkdir(parents=True)
    for index, name in enumerate(entrypoints):
        (codegen / f"{index:064x}.html").write_bytes(
            brotli.compress((frontend_dist / name).read_bytes())
        )
    executable = tmp_path / "agent-friend.exe"
    executable.write_bytes(b"agent-friend-installer-helper")

    try:
        package._validate_embedded_frontend(
            executable,
            frontend_dist,
            entrypoints,
            cargo_target,
        )
    except RuntimeError as error:
        assert "executable role mismatch" in str(error)
        assert "Cargo default-run" in str(error)
    else:
        raise AssertionError("an installer helper must not pass as the desktop shell")


def test_formal_build_rejects_dirty_source(tmp_path) -> None:
    package = _load("package_run_formal_guard", PACKAGE_DIR / "run.py")
    repo = tmp_path / "repo"
    common_git = repo / ".git"
    home = tmp_path / "home"
    common_git.mkdir(parents=True)
    home.mkdir()
    roots = package.resolve_package_roots(
        repo,
        state_dir=tmp_path / "state",
        releases_dir=tmp_path / "releases",
        home=home,
        common_git_dir=common_git,
    )
    project = package.ProjectVersion(
        version=package.read_project_version(package.REPO_ROOT).version,
        values={"tauri": "0.1.7", "frontend": "0.1.7", "cargo": "0.1.7"},
    )

    try:
        package._validate_formal_build(
            roots,
            project.version,
            source={"commit": "a" * 40, "dirty": True},
            distribution="ordinary",
        )
    except RuntimeError as exc:
        assert "clean worktree" in str(exc)
    else:
        raise AssertionError("dirty formal build must fail closed")


def test_frozen_image_capability_runs_all_static_decoders() -> None:
    entry = _load("image_smoke_runtime_entry", PACKAGE_DIR / "runtime_entry.py")
    assert entry._smoke_image_reading() == {
        "formats": ["PNG", "JPEG", "WEBP"],
        "snapshot_roundtrip": True,
    }
