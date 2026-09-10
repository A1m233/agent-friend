"""Run and verify destructive-but-isolated Windows installer upgrade smoke tests."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from install_contract import (
    INSTALLER_SMOKE_PROFILE,
    WINDOWS_INSTALLER_MANUFACTURER,
    InstallerVerificationInputs,
    validate_profile_isolation,
)
from paths import PackageRoots, create_build_paths
from releases import validate_release_directory
from windows_shell_paths import shell_paths, verify_smoke_environment

SMOKE_REPORT_SCHEMA = 3
SMOKE_DISK_MARGIN_BYTES = 512 * 1024 * 1024
REQUIRED_SCENARIOS = {
    "collision_preflight",
    "fresh_install",
    "preinstall_launch_failure_diagnostics",
    "legacy_fixed_layout_upgrade",
    "running_shell_upgrade",
    "owned_orphan_runtime_upgrade",
    "unconfirmed_file_lock_failure_and_retry",
    "identity_mismatch_rejected",
    "silent_success_exit_code",
    "same_version_repair",
    "newer_installed_version_rejected_without_mutation",
    "install_root_drift_rejected_without_move_intent",
    "explicit_program_root_migration",
    "overlong_migration_target_rolls_back",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _smoke_disk_preflight(
    *,
    baseline_runtime: Path,
    candidate_payload: Path,
    installer: Path,
    available_bytes: int,
) -> dict[str, int]:
    required_bytes = (
        _tree_size(baseline_runtime)
        + _tree_size(candidate_payload)
        + installer.stat().st_size
        + SMOKE_DISK_MARGIN_BYTES
    )
    if available_bytes < required_bytes:
        raise RuntimeError(
            "installer smoke requires more free disk space before any install mutation: "
            f"available={available_bytes}, required={required_bytes}"
        )
    return {"availableBytes": available_bytes, "requiredBytes": required_bytes}


def write_build_report(
    roots: PackageRoots,
    *,
    project_version: str,
    baseline_build_id: str,
    candidate_build_id: str,
    candidate_installer: Path,
) -> Path:
    baseline = create_build_paths(roots, build_id=baseline_build_id)
    candidate = create_build_paths(roots, build_id=candidate_build_id)
    baseline_shell = baseline.build_dir / "smoke-baseline-shell.exe"
    required = [baseline_shell, baseline.runtime_stage, candidate_installer]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("installer smoke build output is incomplete: " + ", ".join(missing))
    release_manifest = validate_release_directory(candidate_installer.parent, formal=False)
    if release_manifest.get("profile_id") != INSTALLER_SMOKE_PROFILE.profile_id:
        raise RuntimeError("candidate installer does not use the installer-smoke profile")
    if release_manifest.get("build_id") != candidate_build_id:
        raise RuntimeError("candidate installer manifest build id mismatch")
    raw_verification_inputs = release_manifest.get("installer_verification")
    if not isinstance(raw_verification_inputs, dict):
        raise RuntimeError("candidate installer manifest lacks verification inputs")
    verification_inputs = InstallerVerificationInputs.from_json_dict(raw_verification_inputs)
    output_dir = roots.work_root / "installer-smoke-reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"build-{candidate_build_id}.json"
    value = {
        "schemaVersion": SMOKE_REPORT_SCHEMA,
        "kind": "installer-smoke-build",
        "createdAt": datetime.now(UTC).isoformat(),
        "profileId": INSTALLER_SMOKE_PROFILE.profile_id,
        "projectVersion": project_version,
        "verificationInputs": verification_inputs.to_json_dict(),
        "baseline": {
            "buildId": baseline_build_id,
            "workRelative": baseline.build_dir.relative_to(roots.work_root).as_posix(),
            "shellSha256": sha256(baseline_shell),
        },
        "candidate": {
            "buildId": candidate_build_id,
            "workRelative": candidate.build_dir.relative_to(roots.work_root).as_posix(),
            "installerRelative": candidate_installer.relative_to(roots.releases_root).as_posix(),
            "installerName": candidate_installer.name,
            "installerSha256": sha256(candidate_installer),
        },
    }
    report_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return report_path


def _resolve_build_report(
    roots: PackageRoots,
    report_path: Path,
    *,
    expected_verification_inputs: InstallerVerificationInputs,
) -> tuple[dict[str, Any], Path, Path, Path]:
    report = _read_json(report_path)
    if (
        report.get("schemaVersion") != SMOKE_REPORT_SCHEMA
        or report.get("kind") != "installer-smoke-build"
        or report.get("profileId") != INSTALLER_SMOKE_PROFILE.profile_id
    ):
        raise RuntimeError("unsupported installer smoke build report")
    baseline = report.get("baseline")
    candidate = report.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise RuntimeError("installer smoke build report is incomplete")
    raw_verification_inputs = report.get("verificationInputs")
    if not isinstance(raw_verification_inputs, dict):
        raise RuntimeError("installer smoke build report lacks verification inputs")
    verification_inputs = InstallerVerificationInputs.from_json_dict(raw_verification_inputs)
    if verification_inputs != expected_verification_inputs:
        raise RuntimeError(
            "installer smoke build report package input fingerprint does not match current build"
        )
    baseline_build_id = str(baseline.get("buildId", ""))
    candidate_build_id = str(candidate.get("buildId", ""))
    baseline_paths = create_build_paths(roots, build_id=baseline_build_id)
    candidate_paths = create_build_paths(roots, build_id=candidate_build_id)
    installer = (roots.releases_root / str(candidate.get("installerRelative", ""))).resolve()
    installer.relative_to(roots.releases_root.resolve())
    baseline_shell = baseline_paths.build_dir / "smoke-baseline-shell.exe"
    if sha256(baseline_shell) != baseline.get("shellSha256"):
        raise RuntimeError("baseline shell hash no longer matches build report")
    if sha256(installer) != candidate.get("installerSha256"):
        raise RuntimeError("candidate installer hash no longer matches build report")
    if candidate_paths.build_id != candidate_build_id:
        raise RuntimeError("candidate build path mismatch")
    release = validate_release_directory(installer.parent, formal=False)
    if release.get("installer_verification") != verification_inputs.to_json_dict():
        raise RuntimeError("candidate installer verification inputs no longer match build report")
    return report, baseline_shell, baseline_paths.payload_stage, installer


def _windows_paths() -> dict[str, Path]:
    folders = shell_paths()
    local = folders["local"].resolve()
    roaming = folders["roaming"].resolve()
    install = (local / INSTALLER_SMOKE_PROFILE.install_dir_name).resolve()
    move_parent = (local / "agent-friend-smoke-move").resolve()
    move_install = (move_parent / INSTALLER_SMOKE_PROFILE.install_dir_name).resolve()
    long_move_parent = (
        local / f"{INSTALLER_SMOKE_PROFILE.install_dir_name}-migration-target"
    ).resolve()
    long_move_install = (long_move_parent / INSTALLER_SMOKE_PROFILE.install_dir_name).resolve()
    if install.parent != local or install.name != INSTALLER_SMOKE_PROFILE.install_dir_name:
        raise RuntimeError(f"unsafe installer smoke root: {install}")
    if (
        move_parent.parent != local
        or move_install.parent != move_parent
        or move_install.name != INSTALLER_SMOKE_PROFILE.install_dir_name
        or long_move_parent.parent != local
        or long_move_install.parent != long_move_parent
        or long_move_install.name != INSTALLER_SMOKE_PROFILE.install_dir_name
    ):
        raise RuntimeError(f"unsafe installer smoke migration root: {move_install}")
    return {
        "local": local,
        "roaming": roaming,
        "programs": folders["programs"].resolve(),
        "desktop": folders["desktop"].resolve(),
        "install": install,
        "move_parent": move_parent,
        "move_install": move_install,
        "long_move_parent": long_move_parent,
        "long_move_install": long_move_install,
        "data": (roaming / INSTALLER_SMOKE_PROFILE.data_namespace).resolve(),
        "settings": (roaming / INSTALLER_SMOKE_PROFILE.settings_namespace).resolve(),
        "logs": (local / INSTALLER_SMOKE_PROFILE.log_namespace / "Logs").resolve(),
    }


def _exact_smoke_processes(install_root: Path) -> list[Any]:
    import psutil

    result = []
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            executable = process.info.get("exe")
            if executable and Path(executable).resolve().is_relative_to(install_root):
                result.append(process)
        except (OSError, psutil.Error):
            continue
    return result


def _stop_exact_smoke_processes(install_root: Path) -> None:
    import psutil

    processes = _exact_smoke_processes(install_root)
    for process in processes:
        with suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        process.kill()
    _, alive = psutil.wait_procs(alive, timeout=5)
    if alive:
        raise RuntimeError("installer smoke processes could not be stopped by exact image path")


def _delete_exact(path: Path, expected_parent: Path) -> None:
    target = path.resolve(strict=False)
    if target.parent != expected_parent.resolve() or not target.name:
        raise RuntimeError(f"refusing unsafe installer smoke cleanup target: {target}")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def _shortcut_target(path: Path) -> Path:
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($env:AGENT_FRIEND_SMOKE_SHORTCUT); "
        "[Console]::Out.Write($shortcut.TargetPath)"
    )
    env = os.environ.copy()
    env["AGENT_FRIEND_SMOKE_SHORTCUT"] = str(path)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        env=env,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"could not read isolated installer shortcut: {path.name}")
    return Path(result.stdout.strip()).resolve()


def cleanup_smoke_state() -> None:
    import winreg

    paths = _windows_paths()
    _stop_exact_smoke_processes(paths["install"])
    _stop_exact_smoke_processes(paths["move_install"])
    _stop_exact_smoke_processes(paths["long_move_install"])
    _delete_exact(paths["install"], paths["local"])
    _delete_exact(paths["move_parent"], paths["local"])
    _delete_exact(paths["long_move_parent"], paths["local"])
    _delete_exact(paths["data"], paths["roaming"])
    _delete_exact(paths["settings"], paths["roaming"])
    log_parent = paths["logs"].parent
    _delete_exact(log_parent, paths["local"])

    product = INSTALLER_SMOKE_PROFILE.registry_product_name
    manufacturer = WINDOWS_INSTALLER_MANUFACTURER
    for subkey in (
        rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{product}",
        rf"Software\{manufacturer}\{product}",
    ):
        with suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with (
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE) as key,
        suppress(FileNotFoundError),
    ):
        winreg.DeleteValue(key, INSTALLER_SMOKE_PROFILE.autostart_value_name)
    for shortcut in (
        paths["desktop"] / f"{INSTALLER_SMOKE_PROFILE.product_name}.lnk",
        paths["programs"] / f"{INSTALLER_SMOKE_PROFILE.product_name}.lnk",
    ):
        shortcut.unlink(missing_ok=True)
    start_menu_product_dir = paths["programs"] / INSTALLER_SMOKE_PROFILE.product_name
    programs_dir = start_menu_product_dir.parent
    if start_menu_product_dir.exists():
        _delete_exact(start_menu_product_dir, programs_dir)


def _write_legacy_registry(install_root: Path, *, version: str = "0.0.0") -> None:
    import winreg

    product = INSTALLER_SMOKE_PROFILE.registry_product_name
    uninstall_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{product}"
    manufacturer_path = rf"Software\{WINDOWS_INSTALLER_MANUFACTURER}\{product}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, uninstall_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "1")
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_root))
        winreg.SetValueEx(
            key,
            "MainBinaryName",
            0,
            winreg.REG_SZ,
            f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe",
        )
        winreg.SetValueEx(
            key,
            "UninstallString",
            0,
            winreg.REG_SZ,
            str(install_root / "uninstall.exe"),
        )
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, manufacturer_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(install_root))


def _read_registered_install() -> dict[str, str]:
    import winreg

    product = INSTALLER_SMOKE_PROFILE.registry_product_name
    uninstall_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{product}"
    manufacturer_path = rf"Software\{WINDOWS_INSTALLER_MANUFACTURER}\{product}"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_path) as key:
        version, _ = winreg.QueryValueEx(key, "DisplayVersion")
        location, _ = winreg.QueryValueEx(key, "InstallLocation")
        uninstall, _ = winreg.QueryValueEx(key, "UninstallString")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, manufacturer_path) as key:
        root, _ = winreg.QueryValueEx(key, "")
    return {
        "version": str(version),
        "location": str(location).strip('"'),
        "uninstall": str(uninstall).strip('"'),
        "root": str(root),
    }


def _set_smoke_autostart(install_root: Path) -> None:
    import winreg

    executable = install_root / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe"
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
    ) as key:
        winreg.SetValueEx(
            key,
            INSTALLER_SMOKE_PROFILE.autostart_value_name,
            0,
            winreg.REG_SZ,
            f'"{executable}" --autostart',
        )


def _read_smoke_autostart() -> str:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
    ) as key:
        value, _ = winreg.QueryValueEx(key, INSTALLER_SMOKE_PROFILE.autostart_value_name)
    return str(value)


def _program_tree_fingerprint(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stage_legacy_fixture(
    baseline_shell: Path,
    baseline_runtime: Path,
    *,
    version: str = "0.0.0",
) -> dict[str, str]:
    paths = _windows_paths()
    cleanup_smoke_state()
    install = paths["install"]
    install.mkdir(parents=True)
    shell = install / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe"
    shutil.copy2(baseline_shell, shell)
    shutil.copytree(baseline_runtime, install / "runtime")
    _write_legacy_registry(install, version=version)
    paths["data"].mkdir(parents=True)
    paths["settings"].mkdir(parents=True)
    paths["logs"].mkdir(parents=True)
    (paths["data"] / "installer-smoke-sentinel.txt").write_text("preserve-data\n", encoding="utf-8")
    (paths["settings"] / "installer-smoke-sentinel.txt").write_text(
        "preserve-settings\n", encoding="utf-8"
    )
    (paths["logs"] / "installer-smoke-sentinel.txt").write_text("preserve-logs\n", encoding="utf-8")
    return {"install": str(install), "shell": str(shell)}


def _stage_active_fixture(
    baseline_shell: Path,
    baseline_payload: Path,
    baseline_build_id: str,
) -> dict[str, str]:
    paths = _windows_paths()
    cleanup_smoke_state()
    install = paths["install"]
    install.mkdir(parents=True)
    shell = install / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe"
    shutil.copy2(baseline_shell, shell)
    payload_relative = Path("packages") / baseline_build_id
    payload = install / payload_relative
    payload.parent.mkdir()
    shutil.copytree(baseline_payload, payload)
    identity_path = payload / "build-identity.json"
    manifest_path = payload / "payload-manifest.json"
    identity = _read_json(identity_path)
    if (
        identity.get("buildId") != baseline_build_id
        or identity.get("profileId") != INSTALLER_SMOKE_PROFILE.profile_id
    ):
        raise RuntimeError("baseline active fixture identity mismatch")
    receipt = {
        "schemaVersion": 1,
        "profileId": INSTALLER_SMOKE_PROFILE.profile_id,
        "buildIdentitySha256": sha256(identity_path),
        "buildId": baseline_build_id,
        "payloadRelativePath": payload_relative.as_posix(),
        "payloadManifestSha256": sha256(manifest_path),
        "activatedAt": datetime.now(UTC).isoformat(),
        "transactionId": "installer-smoke-baseline-fixture",
    }
    (install / "active-install.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    _write_legacy_registry(install)
    paths["data"].mkdir(parents=True)
    paths["settings"].mkdir(parents=True)
    return {"install": str(install), "shell": str(shell)}


def _run_installer(
    installer: Path,
    *,
    target: Path | None = None,
    move_install: bool = False,
    extra_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    command = [str(installer), "/S"]
    command.extend(extra_args)
    if move_install:
        command.append("/MOVEINSTALL")
    if target is not None:
        command.append(f"/D={target}")
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _installer_diagnostic_path(build_id: str) -> Path:
    temp = Path(os.environ["TEMP"]).resolve()
    path = (temp / f"{INSTALLER_SMOKE_PROFILE.product_name}-installer-{build_id}.log").resolve()
    if path.parent != temp:
        raise RuntimeError(f"unsafe installer diagnostics path: {path}")
    return path


def _read_installer_diagnostics(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        raise RuntimeError("installer diagnostics file was not created")
    result: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-16").splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise RuntimeError("installer diagnostics line is malformed")
        result.setdefault(key, []).append(value)
    return result


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x80000000, 0, None, 3, 0x80, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise ctypes.WinError()
    try:
        yield
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _scenario(name: str, action: Any) -> dict[str, Any]:
    started = datetime.now(UTC)
    try:
        details = action() or {}
        result = "passed"
        failure = None
    except Exception as error:
        details = {}
        result = "failed"
        message = str(error).replace(str(Path.home()), "<USER_HOME>")
        failure = f"{type(error).__name__}: {message}"
    ended = datetime.now(UTC)
    return {
        "name": name,
        "result": result,
        "startedAt": started.isoformat(),
        "endedAt": ended.isoformat(),
        "details": details,
        "failureReason": failure,
    }


def run_smoke(
    roots: PackageRoots,
    *,
    build_report_path: Path,
    installed_smoke: Any,
    expected_verification_inputs: InstallerVerificationInputs,
) -> Path:
    verify_smoke_environment()
    completed = False
    try:
        output = _run_smoke(
            roots,
            build_report_path=build_report_path,
            installed_smoke=installed_smoke,
            expected_verification_inputs=expected_verification_inputs,
        )
        completed = True
        return output
    finally:
        cleanup_smoke_state()
        if completed:
            build_report = _read_json(build_report_path)
            candidate = build_report.get("candidate")
            if isinstance(candidate, dict):
                _installer_diagnostic_path(str(candidate.get("buildId", ""))).unlink(
                    missing_ok=True
                )


def _run_smoke(
    roots: PackageRoots,
    *,
    build_report_path: Path,
    installed_smoke: Any,
    expected_verification_inputs: InstallerVerificationInputs,
) -> Path:
    validate_profile_isolation()
    build_report, baseline_shell, baseline_payload, installer = _resolve_build_report(
        roots,
        build_report_path,
        expected_verification_inputs=expected_verification_inputs,
    )
    candidate = build_report["candidate"]
    baseline = build_report["baseline"]
    candidate_version = str(build_report["projectVersion"])
    candidate_build_id = str(candidate["buildId"])
    paths = _windows_paths()
    diagnostic_path = _installer_diagnostic_path(candidate_build_id)
    diagnostic_path.unlink(missing_ok=True)
    candidate_payload = create_build_paths(roots, build_id=str(candidate["buildId"])).payload_stage
    disk_preflight = _smoke_disk_preflight(
        baseline_runtime=baseline_payload / "runtime",
        candidate_payload=candidate_payload,
        installer=installer,
        available_bytes=shutil.disk_usage(paths["local"]).free,
    )
    scenarios: list[dict[str, Any]] = []

    scenarios.append(
        _scenario(
            "collision_preflight",
            lambda: {
                "profileId": INSTALLER_SMOKE_PROFILE.profile_id,
                "productionCollision": False,
                "disk": disk_preflight,
            },
        )
    )

    def fresh_install() -> dict[str, Any]:
        cleanup_smoke_state()
        diagnostic_path.unlink(missing_ok=True)
        result = _run_installer(installer)
        if result.returncode != 0:
            raise RuntimeError(f"fresh installer returned {result.returncode}")
        installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
        registered = _read_registered_install()
        if Path(registered["root"]).resolve() != paths["install"]:
            raise RuntimeError("fresh install did not register the isolated install root")
        diagnostics = _read_installer_diagnostics(diagnostic_path)
        if diagnostics.get("installKind") != ["first-install"]:
            raise RuntimeError("fresh install diagnostics did not identify first-install")
        if "0" not in diagnostics.get("helperResult", []):
            raise RuntimeError("fresh install diagnostics did not record helper success")
        return {
            "exitCode": result.returncode,
            "identity": "consistent",
            "installKind": diagnostics["installKind"][0],
            "diagnosticsPersisted": True,
        }

    scenarios.append(_scenario("fresh_install", fresh_install))

    def preinstall_launch_failure_diagnostics() -> dict[str, Any]:
        cleanup_smoke_state()
        diagnostic_path.unlink(missing_ok=True)
        result = _run_installer(
            installer,
            extra_args=("/AGENTFRIENDSMOKEPREINSTALLLAUNCHFAIL",),
        )
        if result.returncode != 32:
            raise RuntimeError(
                f"preinstall launch failure returned {result.returncode}, expected 32"
            )
        if (paths["install"] / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe").exists():
            raise RuntimeError("failed preinstall wrote the main program binary")
        diagnostics = _read_installer_diagnostics(diagnostic_path)
        if diagnostics.get("installKind") != ["first-install"]:
            raise RuntimeError("failed preinstall diagnostics lost first-install identity")
        if diagnostics.get("phase") != ["preinstall"]:
            raise RuntimeError("failed preinstall diagnostics did not identify its phase")
        if diagnostics.get("helperResult") != ["error"]:
            raise RuntimeError("failed preinstall diagnostics did not identify launch failure")
        if diagnostics.get("helperPresentAfterRun") != ["0"]:
            raise RuntimeError("failed preinstall diagnostics did not record helper absence")
        return {
            "exitCode": result.returncode,
            "installKind": diagnostics["installKind"][0],
            "helperResult": diagnostics["helperResult"][0],
            "helperPresentAfterRun": diagnostics["helperPresentAfterRun"][0],
            "diagnosticsPersisted": True,
        }

    scenarios.append(
        _scenario(
            "preinstall_launch_failure_diagnostics",
            preinstall_launch_failure_diagnostics,
        )
    )

    def legacy_upgrade() -> dict[str, Any]:
        _stage_legacy_fixture(baseline_shell, baseline_payload / "runtime")
        result = _run_installer(installer)
        if result.returncode != 0:
            raise RuntimeError(f"silent installer returned {result.returncode}")
        installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
        for sentinel in (
            paths["data"] / "installer-smoke-sentinel.txt",
            paths["settings"] / "installer-smoke-sentinel.txt",
        ):
            if not sentinel.is_file():
                raise RuntimeError("isolated user-state sentinel was not preserved")
        return {"exitCode": result.returncode, "identity": "consistent"}

    scenarios.append(_scenario("legacy_fixed_layout_upgrade", legacy_upgrade))
    scenarios.append(
        _scenario(
            "silent_success_exit_code",
            lambda: (
                {"exitCode": 0}
                if scenarios[-1]["result"] == "passed"
                else (_ for _ in ()).throw(RuntimeError("legacy silent install did not pass"))
            ),
        )
    )

    def same_version_repair() -> dict[str, Any]:
        _stage_legacy_fixture(
            baseline_shell,
            baseline_payload / "runtime",
            version=candidate_version,
        )
        result = _run_installer(installer)
        if result.returncode != 0:
            raise RuntimeError(f"same-version repair returned {result.returncode}")
        installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
        return {"exitCode": result.returncode, "version": candidate_version}

    scenarios.append(_scenario("same_version_repair", same_version_repair))

    def downgrade_rejected() -> dict[str, Any]:
        _stage_legacy_fixture(
            baseline_shell,
            baseline_payload / "runtime",
            version="999.0.0",
        )
        before_tree = _program_tree_fingerprint(paths["install"])
        before_registry = _read_registered_install()
        result = _run_installer(installer)
        if result.returncode != 35:
            raise RuntimeError(f"downgrade rejection returned {result.returncode}, expected 35")
        if _program_tree_fingerprint(paths["install"]) != before_tree:
            raise RuntimeError("downgrade rejection mutated the installed program root")
        if _read_registered_install() != before_registry:
            raise RuntimeError("downgrade rejection mutated registration")
        return {"exitCode": result.returncode, "installedVersion": "999.0.0"}

    scenarios.append(
        _scenario(
            "newer_installed_version_rejected_without_mutation",
            downgrade_rejected,
        )
    )

    def drift_rejected() -> dict[str, Any]:
        _stage_legacy_fixture(baseline_shell, baseline_payload / "runtime")
        before_tree = _program_tree_fingerprint(paths["install"])
        before_registry = _read_registered_install()
        result = _run_installer(installer, target=paths["move_install"])
        if result.returncode == 0:
            raise RuntimeError("silent install-root drift succeeded without move intent")
        if _program_tree_fingerprint(paths["install"]) != before_tree:
            raise RuntimeError("rejected install-root drift mutated the installed program root")
        if _read_registered_install() != before_registry:
            raise RuntimeError("rejected install-root drift mutated registration")
        if paths["move_install"].is_dir() and any(paths["move_install"].iterdir()):
            raise RuntimeError("rejected install-root drift left target program files")
        return {"exitCode": result.returncode, "target": "isolated-migration-root"}

    scenarios.append(_scenario("install_root_drift_rejected_without_move_intent", drift_rejected))

    def overlong_migration_target_rolls_back() -> dict[str, Any]:
        _stage_legacy_fixture(baseline_shell, baseline_payload / "runtime")
        before_tree = _program_tree_fingerprint(paths["install"])
        before_registry = _read_registered_install()
        result = _run_installer(
            installer,
            target=paths["long_move_install"],
            move_install=True,
        )
        if result.returncode == 0:
            raise RuntimeError("overlong migration target unexpectedly succeeded")
        if _program_tree_fingerprint(paths["install"]) != before_tree:
            raise RuntimeError("overlong migration failure mutated the old program root")
        if _read_registered_install() != before_registry:
            raise RuntimeError("overlong migration failure mutated registration")
        if paths["long_move_install"].is_dir() and any(paths["long_move_install"].iterdir()):
            raise RuntimeError("overlong migration failure left target program files")
        return {"exitCode": result.returncode, "oldProgramRootPreserved": True}

    scenarios.append(
        _scenario(
            "overlong_migration_target_rolls_back",
            overlong_migration_target_rolls_back,
        )
    )

    def explicit_program_root_migration() -> dict[str, Any]:
        _stage_legacy_fixture(baseline_shell, baseline_payload / "runtime")
        source = paths["install"]
        target = paths["move_install"]
        _set_smoke_autostart(source)
        result = _run_installer(installer, target=target, move_install=True)
        if result.returncode != 0:
            raise RuntimeError(f"explicit program-root migration returned {result.returncode}")
        installed_smoke(target, profile=INSTALLER_SMOKE_PROFILE)
        retired_program_paths = (
            source / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe",
            source / "uninstall.exe",
            source / "runtime",
            source / "packages",
            source / "active-install.json",
        )
        if any(path.exists() for path in retired_program_paths):
            raise RuntimeError("explicit migration left active program files in the old root")
        if source.is_dir():
            unexpected = sorted(
                path.name for path in source.iterdir() if path.name.casefold() != "logs"
            )
            if unexpected:
                raise RuntimeError(
                    "explicit migration left unexpected old-root entries: " + ", ".join(unexpected)
                )
        registered = _read_registered_install()
        expected_target = str(target)
        if registered["root"] != expected_target or registered["location"] != expected_target:
            raise RuntimeError("explicit migration did not switch registered install root")
        if Path(registered["uninstall"]).parent != target:
            raise RuntimeError("explicit migration did not switch uninstall target")
        if expected_target not in _read_smoke_autostart():
            raise RuntimeError("explicit migration did not switch autostart target")
        expected_executable = (target / f"{INSTALLER_SMOKE_PROFILE.main_binary_name}.exe").resolve()
        start_menu_root = paths["programs"]
        start_menu_candidates = (
            start_menu_root / f"{INSTALLER_SMOKE_PROFILE.product_name}.lnk",
            start_menu_root
            / INSTALLER_SMOKE_PROFILE.product_name
            / f"{INSTALLER_SMOKE_PROFILE.product_name}.lnk",
        )
        start_menu_shortcut = next(
            (path for path in start_menu_candidates if path.is_file()),
            None,
        )
        desktop_shortcut = paths["desktop"] / f"{INSTALLER_SMOKE_PROFILE.product_name}.lnk"
        if start_menu_shortcut is None or not desktop_shortcut.is_file():
            raise RuntimeError("explicit migration did not produce expected shortcuts")
        for shortcut in (start_menu_shortcut, desktop_shortcut):
            if _shortcut_target(shortcut) != expected_executable:
                raise RuntimeError("explicit migration left a shortcut at the old program root")
        for sentinel in (
            paths["data"] / "installer-smoke-sentinel.txt",
            paths["settings"] / "installer-smoke-sentinel.txt",
            paths["logs"] / "installer-smoke-sentinel.txt",
        ):
            if not sentinel.is_file():
                raise RuntimeError("program-root migration moved or deleted user state")
        return {
            "exitCode": result.returncode,
            "oldProgramFilesRetired": True,
            "userStatePreserved": True,
        }

    scenarios.append(_scenario("explicit_program_root_migration", explicit_program_root_migration))

    def running_shell_upgrade() -> dict[str, Any]:
        staged = _stage_active_fixture(
            baseline_shell,
            baseline_payload,
            str(baseline["buildId"]),
        )
        process = subprocess.Popen(
            [staged["shell"]],
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        time.sleep(2)
        if process.poll() is not None:
            raise RuntimeError("baseline shell exited before upgrade")
        result = _run_installer(installer)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as error:
            process.kill()
            raise RuntimeError("installer did not close the owned baseline shell") from error
        if result.returncode != 0:
            raise RuntimeError(f"running-shell upgrade returned {result.returncode}")
        installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
        return {"exitCode": result.returncode, "shellExitCode": process.returncode}

    scenarios.append(_scenario("running_shell_upgrade", running_shell_upgrade))

    def orphan_runtime_upgrade() -> dict[str, Any]:
        _stage_legacy_fixture(baseline_shell, baseline_payload / "runtime")
        launcher = paths["install"] / "runtime" / "python" / "agent-friend-runtime.exe"
        env = os.environ.copy()
        env.update(
            {
                "AGENT_BRIDGE_LIFECYCLE_TOKEN": "installer-smoke-owned-runtime",
                "AGENT_BRIDGE_PORT": str(INSTALLER_SMOKE_PROFILE.bridge_port),
                "AGENT_FRIEND_DATA_DIR": str(paths["data"]),
                "AGENT_FRIEND_LOG_DIR": str(paths["logs"]),
                "AGENT_FRIEND_KEYRING_SERVICE": INSTALLER_SMOKE_PROFILE.keyring_service,
                "AGENT_FRIEND_SETTINGS_BUNDLE_ID": INSTALLER_SMOKE_PROFILE.settings_namespace,
            }
        )
        process = subprocess.Popen(
            [str(launcher), "bridge", "--config-mode", "desktop"],
            cwd=launcher.parent,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Restart Manager stops console applications with a console control
            # event. A new process group still shares the parent's console, while
            # CREATE_NO_WINDOW gives the frozen runtime an isolated console domain.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(2)
        if process.poll() is not None:
            raise RuntimeError("owned legacy runtime exited before upgrade")
        result = _run_installer(installer)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as error:
            process.kill()
            raise RuntimeError("installer did not close the owned legacy runtime") from error
        if result.returncode != 0:
            raise RuntimeError(f"orphan-runtime upgrade returned {result.returncode}")
        installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
        return {"exitCode": result.returncode, "runtimeExitCode": process.returncode}

    scenarios.append(_scenario("owned_orphan_runtime_upgrade", orphan_runtime_upgrade))

    def lock_failure_retry() -> dict[str, Any]:
        staged = _stage_legacy_fixture(baseline_shell, baseline_payload / "runtime")
        shell = Path(staged["shell"])
        before = sha256(shell)
        with _exclusive_file_lock(shell):
            blocked = _run_installer(installer)
        if blocked.returncode == 0:
            raise RuntimeError("unconfirmed file owner unexpectedly allowed installation")
        if sha256(shell) != before or (paths["install"] / "active-install.json").exists():
            raise RuntimeError("failed preinstall modified the active smoke installation")
        retry = _run_installer(installer)
        if retry.returncode != 0:
            raise RuntimeError(f"retry returned {retry.returncode}")
        installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
        return {"blockedExitCode": blocked.returncode, "retryExitCode": retry.returncode}

    scenarios.append(_scenario("unconfirmed_file_lock_failure_and_retry", lock_failure_retry))

    def identity_mismatch() -> dict[str, Any]:
        receipt_path = paths["install"] / "active-install.json"
        receipt = _read_json(receipt_path)
        original = receipt_path.read_bytes()
        receipt["buildId"] = "mismatched-build"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        try:
            try:
                installed_smoke(paths["install"], profile=INSTALLER_SMOKE_PROFILE)
            except RuntimeError:
                return {"identity": "rejected"}
            raise RuntimeError("mismatched active receipt was accepted")
        finally:
            receipt_path.write_bytes(original)

    scenarios.append(_scenario("identity_mismatch_rejected", identity_mismatch))

    release = validate_release_directory(installer.parent, formal=False)
    report = {
        "schemaVersion": SMOKE_REPORT_SCHEMA,
        "kind": "installer-smoke",
        "createdAt": datetime.now(UTC).isoformat(),
        "profileId": INSTALLER_SMOKE_PROFILE.profile_id,
        "verificationInputs": build_report["verificationInputs"],
        "environment": {
            "platform": "windows-x64",
            "accountCategory": "current-user",
        },
        "productionCollisionPreflight": "passed",
        "baseline": {
            "buildId": baseline["buildId"],
            "shellSha256": baseline["shellSha256"],
        },
        "candidate": {
            "buildId": candidate["buildId"],
            "installerName": installer.name,
            "installerRelative": candidate["installerRelative"],
            "installerSha256": release["installer"]["sha256"],
            "version": release["version"],
        },
        "scenarios": scenarios,
        "manualScenarios": [
            {
                "name": "interactive_chinese_english_selector",
                "result": "not-run",
                "reason": "requires visible installer UI observation",
            }
        ],
        "productionUpgrade": {"executed": False, "authorized": False},
    }
    output_dir = roots.work_root / "installer-smoke-reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"smoke-{candidate['buildId']}.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    verify_smoke_report(
        output,
        candidate_build_id=str(candidate["buildId"]),
        candidate_installer=installer,
        expected_verification_inputs=expected_verification_inputs,
    )
    return output


def verify_smoke_report(
    report_path: Path,
    *,
    candidate_build_id: str,
    candidate_installer: Path,
    expected_verification_inputs: InstallerVerificationInputs | None = None,
) -> dict[str, Any]:
    report = _read_json(report_path)
    if (
        report.get("schemaVersion") != SMOKE_REPORT_SCHEMA
        or report.get("kind") != "installer-smoke"
        or report.get("profileId") != INSTALLER_SMOKE_PROFILE.profile_id
    ):
        raise RuntimeError("unsupported installer smoke report")
    candidate = report.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("buildId") != candidate_build_id:
        raise RuntimeError("smoke report candidate build id mismatch")
    if sha256(candidate_installer) != candidate.get("installerSha256"):
        raise RuntimeError("smoke report candidate installer hash mismatch")
    raw_verification_inputs = report.get("verificationInputs")
    if not isinstance(raw_verification_inputs, dict):
        raise RuntimeError("installer smoke report lacks verification inputs")
    verification_inputs = InstallerVerificationInputs.from_json_dict(raw_verification_inputs)
    if (
        expected_verification_inputs is not None
        and verification_inputs != expected_verification_inputs
    ):
        raise RuntimeError(
            "installer smoke report package input fingerprint does not match current build"
        )
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list):
        raise RuntimeError("smoke report scenarios are missing")
    by_name = {
        str(item.get("name")): item
        for item in scenarios
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    missing = sorted(REQUIRED_SCENARIOS.difference(by_name))
    failed = sorted(
        name for name in REQUIRED_SCENARIOS if by_name.get(name, {}).get("result") != "passed"
    )
    if missing or failed:
        raise RuntimeError(
            f"installer smoke report is not passing: missing={missing}, failed={failed}"
        )
    if report.get("productionCollisionPreflight") != "passed":
        raise RuntimeError("installer smoke report lacks production collision preflight")
    return report


def verify_smoke_report_for_reuse(
    report_path: Path,
    *,
    roots: PackageRoots,
    expected_verification_inputs: InstallerVerificationInputs,
) -> dict[str, Any]:
    report = _read_json(report_path)
    candidate = report.get("candidate")
    if not isinstance(candidate, dict):
        raise RuntimeError("installer smoke report candidate is missing")
    relative = str(candidate.get("installerRelative", ""))
    installer = (roots.releases_root / relative).resolve(strict=False)
    try:
        installer.relative_to(roots.releases_root.resolve())
    except ValueError as error:
        raise RuntimeError("installer smoke report candidate path escaped releases root") from error
    release = validate_release_directory(installer.parent, formal=False)
    if release.get("installer_verification") != expected_verification_inputs.to_json_dict():
        raise RuntimeError(
            "candidate installer package input fingerprint does not match current build"
        )
    return verify_smoke_report(
        report_path,
        candidate_build_id=str(candidate.get("buildId", "")),
        candidate_installer=installer,
        expected_verification_inputs=expected_verification_inputs,
    )
