"""Bounded acceptance of an exact, governed installer-smoke desktop candidate."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def wait_runtime_identity(
    shell: subprocess.Popen[bytes], journal: Path, build_id: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while shell.poll() is None and time.monotonic() < deadline:
        if journal.is_file():
            for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("processId") != shell.pid or event.get("kind") != "identity-resolved":
                    continue
                identity = event.get("details", {})
                if (
                    identity.get("runtimeKind") != "installer-smoke"
                    or identity.get("profileId") != "installer-smoke"
                    or identity.get("buildIdentity", {}).get("buildId") != build_id
                ):
                    raise RuntimeError(
                        "running shell identity does not match the exact isolated candidate"
                    )
                return event
        time.sleep(0.1)
    raise RuntimeError("isolated shell did not publish its expected runtime identity")


def candidate_guard(manifest: dict[str, Any], installer: Path, build_id: str) -> None:
    if manifest.get("profile_id") != "installer-smoke":
        raise ValueError("desktop stress only accepts the isolated installer-smoke profile")
    if not build_id or manifest.get("build_id") != build_id:
        raise ValueError("candidate build ID does not match the governed release")
    if manifest.get("installer", {}).get("name") != installer.name:
        raise ValueError("installer is not the exact file verified by the release manifest")


def clean_environment(source: dict[str, str]) -> dict[str, str]:
    # Reuse the source catalog instead of assuming only the current chat provider.
    from runtime_config.credentials import ALL_CREDENTIAL_NAMES, CREDENTIAL_ALIASES

    credential_names = set(ALL_CREDENTIAL_NAMES)
    credential_names.update(alias for aliases in CREDENTIAL_ALIASES.values() for alias in aliases)
    result = {
        key: value
        for key, value in source.items()
        if key not in credential_names
        and not key.upper().startswith(
            ("AGENT_", "VOICE_", "DEEPSEEK_", "OPENAI_", "ANTHROPIC_", "KIMI_", "VOLC_", "LLM_")
        )
        and not key.upper().endswith(("_API_KEY", "_SECRET_KEY", "_ACCESS_TOKEN"))
    }
    result["AGENT_BRIDGE_IM_ENABLED"] = "false"
    result["AGENT_FRIEND_CONFIG_MODE"] = "desktop"
    return result


def native_teardown(pid: int, shell: Path, created: float) -> dict[str, Any]:
    """Close only this owned shell's native windows, then request its loop to quit."""
    from ctypes import wintypes

    import psutil

    def verify_process() -> None:
        process = psutil.Process(pid)
        if process.create_time() != created or Path(process.exe()).resolve() != shell.resolve():
            raise RuntimeError("refusing native action after process identity changed")

    verify_process()
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.restype = wintypes.BOOL
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    user.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user.PostThreadMessageW.restype = wintypes.BOOL
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindow.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    windows: list[dict[str, Any]] = []

    @callback_type
    def collect(hwnd: int, _parameter: int) -> bool:
        owner = wintypes.DWORD()
        thread_id = user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            text = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd, text, len(text))
            rect = wintypes.RECT()
            user.GetWindowRect(hwnd, ctypes.byref(rect))
            windows.append(
                {
                    "hwnd": hwnd,
                    "threadId": thread_id,
                    "title": text.value,
                    "width": rect.right - rect.left,
                    "height": rect.bottom - rect.top,
                }
            )
        return True

    user.EnumWindows(collect, 0)
    titled = [window for window in windows if window["title"].startswith("agent-friend")]
    if not titled:
        raise RuntimeError("no owned AF native window found for controlled teardown")
    # Bubble has a unique product title and is not the user's production window.
    bubbles = [window for window in titled if window["title"] == "agent-friend-bubble"]
    closed = []
    for window in bubbles:
        verify_process()
        if not user.PostMessageW(window["hwnd"], 0x0010, 0, 0):  # WM_CLOSE
            raise ctypes.WinError(ctypes.get_last_error())
        until = time.monotonic() + 3
        while user.IsWindow(window["hwnd"]) and time.monotonic() < until:
            time.sleep(0.05)
        closed.append(
            {"title": window["title"], "destroyed": not bool(user.IsWindow(window["hwnd"]))}
        )
    verify_process()
    if not user.PostThreadMessageW(titled[0]["threadId"], 0x0012, 0, 0):  # WM_QUIT
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "method": "owned-window WM_CLOSE then owner-thread WM_QUIT",
        "windows": closed,
        "userTrayQuit": "not-tested",
    }


def run(installer: Path, build_id: str, artifacts: Path, duration: int) -> bool:
    """Share the package runner's lock, so its matrix cannot clean this live profile."""
    package_path = ROOT / "scripts/package/run.py"
    sys.path.insert(0, str(package_path.parent))
    spec = importlib.util.spec_from_file_location("af_desktop_stress_package_runner", package_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("package lifecycle helpers are unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    roots = package.resolve_package_roots(ROOT)
    with package._package_build_lock(package.package_workspace_lock_path(roots)):
        return _run_locked(installer, build_id, artifacts, duration, roots)


def _run_locked(installer: Path, build_id: str, artifacts: Path, duration: int, roots: Any) -> bool:
    if os.name != "nt":
        raise ValueError("desktop-stress requires Windows; no runtime was started")
    if not 60 <= duration <= 3600:
        raise ValueError("duration must be between 60 and 3600 seconds")
    sys.path.insert(0, str(ROOT / "scripts/package"))
    import psutil
    from install_contract import INSTALLER_SMOKE_PROFILE, validate_profile_isolation
    from installer_smoke import (
        _exact_smoke_processes,
        _read_registered_install,
        _run_installer,
        _smoke_disk_preflight,
        _stop_exact_smoke_processes,
        _windows_paths,
        cleanup_smoke_state,
    )
    from paths import create_build_paths
    from releases import validate_release_directory
    from windows_shell_paths import verify_smoke_environment
    from workspace_gc import PACKAGE_MIN_FREE_BYTES

    verify_smoke_environment()
    validate_profile_isolation()
    installer = installer.resolve(strict=True)
    manifest = validate_release_directory(installer.parent, formal=False)
    candidate_guard(manifest, installer, build_id)
    paths = _windows_paths()
    build_paths = create_build_paths(roots, build_id=build_id)
    available = shutil.disk_usage(paths["local"]).free
    if build_paths.payload_stage.is_dir() and build_paths.runtime_stage.is_dir():
        disk = _smoke_disk_preflight(
            baseline_runtime=build_paths.runtime_stage,
            candidate_payload=build_paths.payload_stage,
            installer=installer,
            available_bytes=available,
        )
    elif available >= PACKAGE_MIN_FREE_BYTES:
        disk = {"availableBytes": available, "requiredBytes": PACKAGE_MIN_FREE_BYTES}
    else:
        raise RuntimeError(
            "candidate staging is absent and the standard package disk headroom is unavailable"
        )
    for key in ("install", "data", "settings", "logs", "move_parent", "long_move_parent"):
        if paths[key].exists():
            raise ValueError(f"existing isolated state must not be taken over: {key}")
    try:
        _read_registered_install()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("an isolated installation is already registered")
    if _exact_smoke_processes(paths["install"]):
        raise ValueError("isolated profile already running")
    from runtime_config.credentials import ALL_CREDENTIAL_NAMES
    from runtime_config.sources import KeyringSource

    isolated_keyring = KeyringSource(service_name=INSTALLER_SMOKE_PROFILE.keyring_service)
    if any(isolated_keyring.status(name).configured for name in ALL_CREDENTIAL_NAMES):
        raise ValueError("isolated keyring contains credentials; refusing to reuse or delete them")
    artifacts = artifacts.resolve()
    if any(artifacts.is_relative_to(paths[key]) for key in ("install", "data", "settings", "logs")):
        raise ValueError("evidence output must be outside the owned cleanup paths")
    artifacts.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "ok": False,
        "candidate": manifest,
        "requestedDurationSeconds": duration,
        "productionTouched": False,
        "disk": disk,
    }
    env = clean_environment(dict(os.environ))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        cdp_port = probe.getsockname()[1]
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--remote-debugging-port={cdp_port} --remote-debugging-address=127.0.0.1"
    )
    shell = paths["install"] / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe"
    normal: subprocess.Popen[bytes] | None = None
    try:
        result = _run_installer(installer)
        if result.returncode:
            raise ValueError(f"isolated installer failed with {result.returncode}")
        receipt = json.loads((paths["install"] / "active-install.json").read_text(encoding="utf-8"))
        if receipt.get("buildId") != build_id or receipt.get("profileId") != "installer-smoke":
            raise ValueError("installed receipt does not match the exact candidate")
        report["receipt"] = receipt
        normal = subprocess.Popen(
            [str(shell)],
            cwd=shell.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        created = psutil.Process(normal.pid).create_time()
        report.update({"pid": normal.pid, "processCreated": created, "cdpPort": cdp_port})
        report["runtimeIdentity"] = wait_runtime_identity(
            normal, paths["logs"] / "tauri.log.runtime", build_id
        )
        command = [
            "node",
            str(Path(__file__).with_name("desktop-observe.mjs")),
            str(cdp_port),
            str(artifacts),
            str(duration),
            build_id,
        ]
        with (artifacts / "observer.log").open("x", encoding="utf-8") as output:
            observer = subprocess.run(
                command,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=duration + 240,
            )
        report["observerExitCode"] = observer.returncode
        observation = artifacts / "desktop-observation.json"
        if observation.exists():
            report["desktop"] = json.loads(observation.read_text(encoding="utf-8"))
        if observer.returncode or normal.poll() is not None:
            raise RuntimeError("isolated desktop observer failed or shell exited during stress")
        report["teardown"] = native_teardown(normal.pid, shell, created)
        report["exitCode"] = normal.wait(timeout=20)
        report["ok"] = bool(report.get("desktop", {}).get("ok") and normal.returncode == 0)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        try:
            _stop_exact_smoke_processes(paths["install"])
            for source in paths["logs"].glob("tauri.log.runtime*"):
                shutil.copy2(source, artifacts / source.name)
            crash_root = paths["logs"] / "native-crashes"
            if crash_root.exists():
                # Summary only. Never silently export raw process memory.
                for source in crash_root.rglob("*.json"):
                    target = artifacts / "native-crash-summaries" / source.relative_to(crash_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            cleanup_smoke_state()
            report["cleanup"] = "completed-owned-installer-smoke-profile"
        except (OSError, RuntimeError) as error:
            report.update({"ok": False, "cleanupError": str(error)})
        (artifacts / "desktop-stress-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"ok": report["ok"], "report": str(artifacts / "desktop-stress-report.json")}))
    return bool(report["ok"])
