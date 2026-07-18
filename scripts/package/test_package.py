from __future__ import annotations

import importlib.util
import json
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


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_version_is_consistent() -> None:
    package = _load("package_run", PACKAGE_DIR / "run.py")
    assert package._project_version() == "0.1.7"


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
    payload = tmp_path / "trusted-credential-seed.bin"
    key = tmp_path / "trusted-credential-seed.key"
    config_path = tmp_path / "trusted-tauri.package.conf.json"
    base_config = tmp_path / "tauri.package.conf.json"
    base_config.write_text(
        json.dumps({"bundle": {"resources": {"package-runtime/runtime/": "runtime"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(package, "TRUSTED_SEED_PAYLOAD", payload)
    monkeypatch.setattr(package, "TRUSTED_SEED_KEY", key)
    monkeypatch.setattr(package, "TRUSTED_TAURI_CONFIG", config_path)
    monkeypatch.setattr(package, "TAURI_CONFIG", base_config)
    store = _FakeCredentialStore()
    credentials = {
        name: f"trusted-package-never-print-{index}"
        for index, name in enumerate(REQUIRED_CREDENTIAL_NAMES)
    }
    store.values.update(credentials)

    assert package._prepare_trusted_resources(store) == config_path
    assert payload.is_file()
    assert key.is_file()
    config = json.loads(config_path.read_text("utf-8"))
    resources = config["bundle"]["resources"]
    assert resources["package-runtime/trusted-credential-seed.bin"] == (
        "trusted-credential-seed.bin"
    )
    assert resources["package-runtime/trusted-credential-seed.key"] == (
        "trusted-credential-seed.key"
    )
    decoded = decrypt_credential_seed(payload.read_bytes(), key.read_bytes())
    for name in OPTIONAL_CREDENTIAL_NAMES:
        assert decoded[name] is None
    for value in credentials.values():
        encoded = value.encode()
        assert encoded not in payload.read_bytes()
        assert encoded not in key.read_bytes()
        assert value not in config_path.read_text("utf-8")


def test_trusted_artifact_paths_are_visibly_sensitive() -> None:
    package = _load("package_run_artifact_paths", PACKAGE_DIR / "run.py")

    ordinary_dir, ordinary_name = package._release_artifact_paths("1.2.3", trusted_beta=False)
    trusted_dir, trusted_name = package._release_artifact_paths("1.2.3", trusted_beta=True)

    assert ordinary_dir.name == "agent-friend-1.2.3-windows-x64"
    assert ordinary_name == "agent-friend_1.2.3_windows_x64-setup.exe"
    assert "trusted-beta-sensitive" in trusted_dir.name
    assert "trusted-beta-SENSITIVE" in trusted_name
