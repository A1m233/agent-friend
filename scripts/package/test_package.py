from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

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


def test_frozen_smoke_uses_an_explicit_non_secret_provider_placeholder() -> None:
    source = (PACKAGE_DIR / "run.py").read_text("utf-8")
    assert '"DEEPSEEK_API_KEY"] = "package-smoke-not-a-real-credential"' in source


def test_installed_smoke_requires_complete_packaged_runtime(tmp_path) -> None:
    package = _load("package_run_installed_smoke", PACKAGE_DIR / "run.py")

    try:
        package._smoke_installed_runtime(tmp_path)
    except RuntimeError as exc:
        assert "installed runtime is incomplete" in str(exc)
        assert "agent-friend-runtime.exe" in str(exc)
    else:
        raise AssertionError("incomplete installed runtime must fail closed")


def test_cloudflared_lock_has_pinned_version_and_hash() -> None:
    lock = json.loads((PACKAGE_DIR / "vendor" / "cloudflared.lock.json").read_text("utf-8"))
    assert lock["version"] != "latest"
    assert len(lock["sha256"]) == 64
    assert lock["url"].endswith("cloudflared-windows-amd64.exe")


def test_xiaohongshu_license_is_pinned_to_the_source_commit() -> None:
    package = _load("package_run_xiaohongshu_license", PACKAGE_DIR / "run.py")

    assert package.XIAOHONGSHU_LICENSE_URL.endswith(
        "/3aacfb3583482424c0170e86e05725df940dd35e/LICENSE"
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
    headless.parent.mkdir(parents=True)
    go_mod.parent.mkdir(parents=True)
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
    parent.mkdir(exist_ok=True)
    (parent / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=parent, check=True)

    builder._apply_patch(workspace)

    assert "Leakless(false)" in headless.read_text(encoding="utf-8")
    assert "replace github.com/xpzouying/headless_browser" in go_mod.read_text(encoding="utf-8")


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
    assert '"agent-friend" "$\\"$INSTDIR\\${MAINBINARYNAME}.exe$\\" --autostart"' in hooks


def test_nsis_uninstall_uses_tauri_cleanup_without_touching_private_startup_state() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")

    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert "DeleteRegValue" not in hooks
    assert "StartupApproved" not in hooks


def test_tauri_cli_is_pinned_for_the_verified_nsis_template() -> None:
    package_json = json.loads(
        (PACKAGE_DIR.parent.parent / "frontend" / "package.json").read_text("utf-8")
    )
    assert package_json["devDependencies"]["@tauri-apps/cli"] == "2.11.2"


def test_nsis_install_uninstall_refuse_to_kill_a_running_app() -> None:
    hooks = (PACKAGE_DIR / "windows" / "nsis-hooks.nsh").read_text("utf-8")

    assert "NSIS_HOOK_PREINSTALL" in hooks
    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert 'FindProcessCurrentUser "${executableName}"' in hooks
    assert 'ReadRegStr $R4 SHCTX "${UNINSTKEY}" "MainBinaryName"' in hooks
    assert "IfSilent app_closed_noninteractive_" in hooks
    assert "${If} $PassiveMode = 1" in hooks
    assert "SetErrorLevel 32" in hooks
    assert "!macroundef CheckIfAppIsRunning" in hooks
    assert "!macro CheckIfAppIsRunning executableName productName" in hooks
    assert "KillProcess" not in hooks


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


def test_doctor_without_configured_model_remains_an_import_smoke(monkeypatch) -> None:
    runtime_entry = _load("runtime_entry_without_model", PACKAGE_DIR / "runtime_entry.py")
    monkeypatch.delenv("AGENT_FRIEND_MEMORY_MODEL_DIR", raising=False)

    assert runtime_entry._doctor() == 0


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
    assert 'seed-credentials --payload "$R1" --key-file "$R2"' in hooks
    assert 'Delete "$R1"' in hooks
    assert 'Delete "$R2"' in hooks
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

    config_path = package._write_package_config(
        build_paths,
        local_test=True,
        source={"commit": "a" * 40, "dirty": True},
        trusted_beta=True,
        credential_store=store,
    )
    payload, key = package._trusted_resource_paths(build_paths)
    assert payload.is_file()
    assert key.is_file()
    config = json.loads(config_path.read_text("utf-8"))
    resources = config["bundle"]["resources"]
    assert resources[payload.as_posix()] == "trusted-credential-seed.bin"
    assert resources[key.as_posix()] == "trusted-credential-seed.key"
    assert resources[build_paths.runtime_stage.as_posix() + "/"] == "runtime"
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


def test_project_version_set_updates_all_product_metadata(tmp_path) -> None:
    versioning = _load("package_versioning_update", PACKAGE_DIR / "versioning.py")
    _write_version_fixture(tmp_path)

    result = versioning.set_project_version(tmp_path, "1.3.0-beta.1")

    assert str(result.version) == "1.3.0-beta.1"
    assert set(result.values.values()) == {"1.3.0-beta.1"}


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
    build_paths.runtime_stage.mkdir(parents=True)
    (build_paths.runtime_stage / "runtime-inventory.json").write_text("{}", encoding="utf-8")
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
    published: dict[str, object] = {}

    def fake_run(command, *, cwd=package.REPO_ROOT, env=None) -> None:
        assert env is not None
        if command[1:3] == ["run", "build"]:
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
        assert stale_marker.exists()
        config_path = Path(command[command.index("--config") + 1])
        config = json.loads(config_path.read_text("utf-8"))
        resources = config["bundle"]["resources"]
        assert "package-runtime/runtime/" not in resources
        runtime_resource = build_paths.runtime_stage.as_posix() + "/"
        assert resources[runtime_resource] == "runtime"
        assert config["build"]["beforeBuildCommand"] == ""
        assert config["build"]["frontendDist"] == package._tauri_relative_path(
            build_paths.frontend_dist
        )
        bundle = build_paths.cargo_target_dir / "release" / "bundle" / "nsis"
        bundle.mkdir(parents=True)
        (bundle / "candidate.exe").write_bytes(b"candidate")
        executable = build_paths.cargo_target_dir / "release" / "agent-friend.exe"
        executable.write_bytes(
            b"\0".join(name.encode("utf-8") for name in package.REQUIRED_FRONTEND_ENTRYPOINTS)
        )

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

    result = package._build_installer(
        "1.2.3",
        build_paths=build_paths,
        source={"commit": "a" * 40, "dirty": True},
        local_test=True,
    )

    assert result == tmp_path / "published.exe"
    assert published["local_test"] is True
    assert published["releases_root"] == roots.releases_root
    manifest = published["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["build_mode"] == "local-test"
    assert manifest["publishable"] is False
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
    executable = tmp_path / "agent-friend.exe"
    executable.write_bytes(b"index.html\0chat.html")

    try:
        package._validate_embedded_frontend(
            executable,
            ["index.html", "chat.html", "pet.html"],
        )
    except RuntimeError as error:
        assert "does not contain the complete frontend" in str(error)
        assert "pet.html" in str(error)
    else:
        raise AssertionError("stale Tauri executable must be rejected")


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
            project,
            source={"commit": "a" * 40, "dirty": True},
            distribution="ordinary",
        )
    except RuntimeError as exc:
        assert "clean worktree" in str(exc)
    else:
        raise AssertionError("dirty formal build must fail closed")
