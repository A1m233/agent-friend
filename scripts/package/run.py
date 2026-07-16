"""Build the self-contained Windows installer without reading user credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
TAURI_DIR = REPO_ROOT / "frontend" / "src-tauri"
STAGE_DIR = TAURI_DIR / "package-runtime" / "runtime"
OUTPUT_DIR = REPO_ROOT / "package-output"
CACHE_DIR = SCRIPT_DIR / ".cache"
SPEC_PATH = SCRIPT_DIR / "windows" / "agent_friend_runtime.spec"
TAURI_CONFIG = SCRIPT_DIR / "windows" / "tauri.package.conf.json"
CLOUDFLARED_LOCK = SCRIPT_DIR / "vendor" / "cloudflared.lock.json"
MODEL_RELATIVE_DIR = Path("models") / "bge-small-zh-v1.5"
MODEL_REPOSITORY = "onnx-community/bge-small-zh-v1.5-ONNX"
MODEL_REVISION = "9507db33464b5da99a532ac26b2a251767cbc62b"
SECRET_NAMES = {
    "DEEPSEEK_API_KEY",
    "TAVILY_API_KEY",
    "VOLC_ACCESS_KEY",
    "VOLC_SECRET_KEY",
    "VOLC_SPEECH_API_KEY",
    "VOICE_BRIDGE_VOLC_ACCESS_KEY",
    "VOICE_BRIDGE_VOLC_SECRET_KEY",
    "VOICE_BRIDGE_VOLC_SPEECH_API_KEY",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitized_env() -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in SECRET_NAMES
        and not name.upper().endswith(("_API_KEY", "_SECRET_KEY"))
    }
    env["AGENT_FRIEND_CONFIG_MODE"] = "env-only"
    return env


def _run(command: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_readiness(port: int, token: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    url = f"http://127.0.0.1:{port}/internal/lifecycle/readiness"
    request = urllib.request.Request(url, headers={"X-Agent-Friend-Lifecycle-Token": token})
    last_error = "service did not respond"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"frozen service exited before readiness: {return_code}")
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ready":
                return
            last_error = f"unexpected readiness: {payload}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"frozen service readiness timed out: {last_error}")


def _shutdown_frozen_service(
    service: str, port: int, token: str, process: subprocess.Popen[bytes]
) -> None:
    if process.poll() is not None:
        return
    body = b"{}" if service == "voice" else b'{"grace_timeout":1,"cancel_timeout":1}'
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/internal/lifecycle/shutdown",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Friend-Lifecycle-Token": token,
        },
    )
    try:
        urllib.request.urlopen(request, timeout=3).close()
        process.wait(timeout=8)
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _smoke_frozen_services(launcher: Path, base_env: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-friend-frozen-smoke-") as temp_dir:
        root = Path(temp_dir)
        for service, port_env in (
            ("bridge", "AGENT_BRIDGE_PORT"),
            ("voice", "VOICE_BRIDGE_PORT"),
        ):
            port = _free_loopback_port()
            token = f"package-smoke-{service}"
            env = dict(base_env)
            env.update(
                {
                    "AGENT_FRIEND_DATA_DIR": str(root / service / "data"),
                    "AGENT_FRIEND_LOG_DIR": str(root / service / "logs"),
                    "AGENT_FRIEND_LIFECYCLE_TOKEN": token,
                    "AGENT_BRIDGE_IM_ENABLED": "false",
                    port_env: str(port),
                }
            )
            if service == "bridge":
                # Bridge 在装配阶段要求 provider credential；用进程内固定假值覆盖
                # 启动闭包即可。Smoke 只访问 readiness，绝不发起 LLM 请求。
                env["DEEPSEEK_API_KEY"] = "package-smoke-not-a-real-credential"
            log_path = root / f"{service}.log"
            with log_path.open("wb") as log_file:
                process = subprocess.Popen(
                    [str(launcher), service, "--config-mode", "env-only"],
                    cwd=launcher.parent,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                try:
                    _wait_for_readiness(port, token, process)
                except Exception as exc:
                    process.terminate()
                    process.wait(timeout=5)
                    output = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    raise RuntimeError(f"{service} frozen smoke failed: {exc}\n{output}") from exc
                finally:
                    _shutdown_frozen_service(service, port, token, process)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _project_version() -> str:
    tauri = _read_json(TAURI_DIR / "tauri.conf.json")
    frontend = _read_json(REPO_ROOT / "frontend" / "package.json")
    cargo_version = ""
    in_package = False
    for raw_line in (TAURI_DIR / "Cargo.toml").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_package = line == "[package]"
        elif in_package and line.startswith("version ="):
            cargo_version = line.split("=", 1)[1].strip().strip('"')
            break
    versions = {str(tauri.get("version")), str(frontend.get("version")), cargo_version}
    if len(versions) != 1 or not cargo_version:
        raise RuntimeError(f"version mismatch across tauri/package.json/Cargo.toml: {versions}")
    return cargo_version


def _ensure_windows_x64() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Windows x64 packaging must run on a Windows x64 build machine")


def _download_locked(lock_path: Path, destination: Path, *, offline: bool) -> Path:
    lock = _read_json(lock_path)
    return _download(
        url=str(lock["url"]),
        expected_sha256=str(lock["sha256"]),
        destination=destination,
        offline=offline,
    )


def _download(*, url: str, expected_sha256: str, destination: Path, offline: bool) -> Path:
    expected = expected_sha256.lower()
    if destination.is_file() and _sha256(destination) == expected:
        return destination
    if offline:
        raise RuntimeError(f"offline cache missing or invalid: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    print(f"download: {url}")
    urllib.request.urlretrieve(url, temporary)
    actual = _sha256(temporary)
    if actual != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch for {url}: expected {expected}, got {actual}")
    temporary.replace(destination)
    return destination


def _install_memory_model(destination: Path) -> None:
    from memory_eval.retrieval_benchmark.runner import install_onnx_assets

    cached_model = CACHE_DIR / f"memory-model-{MODEL_REVISION}"
    if not cached_model.is_dir() or not any(cached_model.iterdir()):
        try:
            install_onnx_assets(
                cached_model,
                model_repo=MODEL_REPOSITORY,
                model_revision=MODEL_REVISION,
            )
        except PermissionError:
            staging = cached_model.with_name(f"{cached_model.name}.staging")
            if cached_model.exists() or not staging.is_dir():
                raise
            # pathlib.Path.replace can reject directory publication on some Windows setups.
            shutil.copytree(staging, cached_model)
            shutil.rmtree(staging)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(cached_model, destination)


def _build_runtime(*, offline: bool, reuse_frozen: bool) -> None:
    work_dir = OUTPUT_DIR / "work" / "pyinstaller"
    dist_dir = work_dir / "dist"
    frozen_launcher = dist_dir / "agent-friend-runtime" / "agent-friend-runtime.exe"
    if reuse_frozen:
        if not frozen_launcher.is_file():
            raise RuntimeError("--reuse-frozen requested but no previous frozen runtime exists")
    else:
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)
        _run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(dist_dir),
                "--workpath",
                str(work_dir / "build"),
                str(SPEC_PATH),
            ]
        )

    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)
    shutil.copytree(dist_dir / "agent-friend-runtime", STAGE_DIR / "python")
    _copy_runtime_supplements(STAGE_DIR / "python")

    cloudflared_lock = _read_json(CLOUDFLARED_LOCK)
    cloudflared_cache = CACHE_DIR / f"cloudflared-{cloudflared_lock['version']}-windows-amd64.exe"
    cloudflared = _download_locked(CLOUDFLARED_LOCK, cloudflared_cache, offline=offline)
    vendor_dir = STAGE_DIR / "vendor"
    vendor_dir.mkdir()
    shutil.copy2(cloudflared, vendor_dir / "cloudflared.exe")
    shutil.copy2(CLOUDFLARED_LOCK, vendor_dir / "cloudflared.lock.json")
    cloudflared_license = _download(
        url=str(cloudflared_lock["license_url"]),
        expected_sha256=str(cloudflared_lock["license_sha256"]),
        destination=CACHE_DIR / f"cloudflared-{cloudflared_lock['version']}-LICENSE",
        offline=offline,
    )
    shutil.copy2(cloudflared_license, vendor_dir / "LICENSE-cloudflared.txt")

    _install_memory_model(STAGE_DIR / MODEL_RELATIVE_DIR)
    (STAGE_DIR / "THIRD-PARTY-NOTICES.txt").write_text(
        "cloudflared\n"
        f"Version: {cloudflared_lock['version']}\n"
        f"Source: {cloudflared_lock['source']}\n"
        "License: Apache License 2.0 (see vendor/LICENSE-cloudflared.txt)\n",
        encoding="utf-8",
    )

    doctor_env = _sanitized_env()
    doctor_env["AGENT_FRIEND_MEMORY_MODEL_DIR"] = str(STAGE_DIR / MODEL_RELATIVE_DIR)
    staged_launcher = STAGE_DIR / "python" / "agent-friend-runtime.exe"
    _run([str(staged_launcher), "doctor"], env=doctor_env)
    _smoke_frozen_services(staged_launcher, doctor_env)
    _write_runtime_inventory(STAGE_DIR)


def _copy_runtime_supplements(frozen_root: Path) -> None:
    import litellm

    litellm_root = Path(litellm.__file__).resolve().parent
    tokenizer_assets = litellm_root / "litellm_core_utils" / "tokenizers"
    if not tokenizer_assets.is_dir():
        raise RuntimeError(f"LiteLLM tokenizer assets are missing: {tokenizer_assets}")
    destination = frozen_root / "_internal" / "litellm" / "litellm_core_utils" / "tokenizers"
    shutil.copytree(tokenizer_assets, destination, dirs_exist_ok=True)
    frozen_litellm = frozen_root / "_internal" / "litellm"
    excluded_roots = {"enterprise", "integrations", "proxy", "rag"}
    for source in litellm_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(litellm_root)
        if relative.parts and relative.parts[0] in excluded_roots:
            continue
        if source.suffix in {".py", ".pyc"}:
            continue
        target = frozen_litellm / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _write_runtime_inventory(root: Path) -> None:
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    (root / "runtime-inventory.json").write_text(
        json.dumps({"schema_version": 1, "files": files}, indent=2),
        encoding="utf-8",
    )


def _build_installer(version: str) -> Path:
    if not (STAGE_DIR / "runtime-inventory.json").is_file():
        raise RuntimeError("runtime stage missing; run the runtime step first")
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is required on the Windows build machine")
    _run(
        [pnpm, "tauri", "build", "--config", str(TAURI_CONFIG), "--bundles", "nsis"],
        cwd=REPO_ROOT / "frontend",
        env=_sanitized_env(),
    )
    bundle_dir = TAURI_DIR / "target" / "release" / "bundle" / "nsis"
    candidates = sorted(bundle_dir.glob("*.exe"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        raise RuntimeError(f"NSIS installer not found in {bundle_dir}")
    release_dir = OUTPUT_DIR / f"agent-friend-{version}-windows-x64"
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)
    setup = release_dir / f"agent-friend_{version}_windows_x64-setup.exe"
    shutil.copy2(candidates[-1], setup)
    digest = _sha256(setup)
    (release_dir / f"{setup.name}.sha256").write_text(f"{digest}  {setup.name}\n", encoding="ascii")
    manifest = {
        "schema_version": 1,
        "product": "agent-friend",
        "version": version,
        "platform": "windows-x64",
        "created_at": datetime.now(UTC).isoformat(),
        "installer": {"name": setup.name, "size": setup.stat().st_size, "sha256": digest},
        "upgrade_mode": "manual-installer",
        "identifier": "com.agentfriend.desktop",
    }
    (release_dir / "release.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return setup


def _smoke_installed_runtime(install_dir: Path) -> None:
    install_root = install_dir.resolve()
    main_binary = install_root / "agent-friend.exe"
    runtime_dir = install_root / "runtime"
    launcher = runtime_dir / "python" / "agent-friend-runtime.exe"
    inventory = runtime_dir / "runtime-inventory.json"
    model_dir = runtime_dir / MODEL_RELATIVE_DIR
    missing = [path for path in (main_binary, launcher, inventory, model_dir) if not path.exists()]
    if missing:
        formatted = ", ".join(str(path) for path in missing)
        raise RuntimeError(f"installed runtime is incomplete: {formatted}")

    smoke_env = _sanitized_env()
    smoke_env["AGENT_FRIEND_MEMORY_MODEL_DIR"] = str(model_dir)
    _run([str(launcher), "doctor"], cwd=launcher.parent, env=smoke_env)
    _smoke_frozen_services(launcher, smoke_env)
    print(f"installed runtime smoke passed: {install_dir.resolve()}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "step",
        choices=("runtime", "installer", "all", "installed-smoke"),
        nargs="?",
        default="all",
    )
    parser.add_argument(
        "--offline", action="store_true", help="require locked assets in local cache"
    )
    parser.add_argument(
        "--reuse-frozen",
        action="store_true",
        help="reuse a previous PyInstaller result while retrying asset staging",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        help="installed application directory used by the installed-smoke step",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _ensure_windows_x64()
    if args.step == "installed-smoke":
        if args.install_dir is None:
            raise RuntimeError("installed-smoke requires --install-dir")
        _smoke_installed_runtime(args.install_dir)
        return 0
    version = _project_version()
    OUTPUT_DIR.mkdir(exist_ok=True)
    if args.step in {"runtime", "all"}:
        _build_runtime(offline=args.offline, reuse_frozen=args.reuse_frozen)
    if args.step in {"installer", "all"}:
        setup = _build_installer(version)
        print(f"installer: {setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
