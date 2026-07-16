from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

PACKAGE_DIR = Path(__file__).resolve().parent


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_version_is_consistent() -> None:
    package = _load("package_run", PACKAGE_DIR / "run.py")
    assert package._project_version() == "0.1.5"


def test_packaging_environment_removes_credentials(monkeypatch) -> None:
    package = _load("package_run_env", PACKAGE_DIR / "run.py")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    monkeypatch.setenv("SOME_VENDOR_SECRET_KEY", "must-not-leak")
    monkeypatch.setenv("PATH", "kept")

    env = package._sanitized_env()

    assert "DEEPSEEK_API_KEY" not in env
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


def test_nsis_uninstall_cleans_the_fixed_autostart_entry() -> None:
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
    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert (
        'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "agent-friend"'
        in hooks
    )


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


def test_doctor_without_configured_model_remains_an_import_smoke(monkeypatch) -> None:
    runtime_entry = _load("runtime_entry_without_model", PACKAGE_DIR / "runtime_entry.py")
    monkeypatch.delenv("AGENT_FRIEND_MEMORY_MODEL_DIR", raising=False)

    assert runtime_entry._doctor() == 0
