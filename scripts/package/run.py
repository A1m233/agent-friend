"""Build the self-contained Windows installer without reading user credentials."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
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
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import brotli
from dependency_policy import validate_frozen_runtime_dependency_policy
from install_contract import (
    ACTIVE_INSTALL_FILE,
    INSTALL_PROFILES,
    PRODUCTION_PROFILE,
    WINDOWS_INSTALLER_MANUFACTURER,
    BuildIdentity,
    InstalledVersionState,
    InstallerVerificationInputs,
    InstallProfile,
    PackageVerificationLevel,
    build_identity,
    build_installer_verification_inputs,
    canonical_json_bytes,
    canonical_windows_install_root,
    classify_installed_version,
    classify_installer_impact,
    max_package_verification_level,
    payload_install_relative_path,
    read_active_install_receipt,
    read_build_identity,
    read_payload_manifest,
    resolve_install_profile,
    safe_relative_path,
    validate_payload_manifest,
    validate_profile_isolation,
    write_canonical_json,
    write_payload_manifest,
)
from installer_smoke import (
    run_smoke as run_installer_smoke,
)
from installer_smoke import (
    verify_smoke_report,
    verify_smoke_report_for_reuse,
)
from installer_smoke import (
    write_build_report as write_installer_smoke_build_report,
)
from paths import (
    BuildPaths,
    PackageRoots,
    create_build_paths,
    load_build_paths,
    new_build_id,
    resolve_package_roots,
    safe_cache_rmtree,
    safe_rmtree,
)
from releases import (
    ReleaseRecord,
    discover_formal_releases,
    formal_release_dir,
    latest_formal_version,
    publish_installer,
    sha256,
)
from versioning import (
    ProjectVersion,
    SemVer,
    configure_trusted_beta_channel,
    next_trusted_beta_version,
    read_project_version,
    read_trusted_beta_channel,
    set_project_version,
    trusted_beta_channel_path,
)
from workspace_gc import (
    apply_workspace_gc,
    cleanup_build_after_success,
    cleanup_smoke_builds_after_success,
    ensure_package_disk_headroom,
    mark_workspace_used,
    package_workspace_lock_path,
    workspace_inventory,
)

from runtime_config import (
    ALL_CREDENTIAL_NAMES,
    KeyringSource,
    collect_trusted_credentials,
    encrypt_credential_seed,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
TAURI_DIR = REPO_ROOT / "frontend" / "src-tauri"
SPEC_PATH = SCRIPT_DIR / "windows" / "agent_friend_runtime.spec"
TAURI_CONFIG = SCRIPT_DIR / "windows" / "tauri.package.conf.json"
TAURI_NSIS_TEMPLATE_URL = (
    "https://raw.githubusercontent.com/tauri-apps/tauri/499df79/"
    "crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi"
)
TAURI_NSIS_TEMPLATE_SHA256 = "ee84148e405adc4d736a46456dd8345a644751bd1f28a335dd7fd833a32d7c3e"
CLOUDFLARED_LOCK = SCRIPT_DIR / "vendor" / "cloudflared.lock.json"
MODEL_RELATIVE_DIR = Path("models") / "bge-small-zh-v1.5"
MODEL_REPOSITORY = "onnx-community/bge-small-zh-v1.5-ONNX"
MODEL_REVISION = "9507db33464b5da99a532ac26b2a251767cbc62b"
REQUIRED_FRONTEND_ENTRYPOINTS = {
    "index.html",
    "bubble.html",
    "im-connect.html",
    "memory-inspector.html",
    "spine-spike.html",
    "live2d-debugger.html",
    "voice-call.html",
    "chat.html",
    "pet.html",
    "settings.html",
}
DESKTOP_SHELL_ROLE_MARKER = b"agent-friend-executable-role:desktop-shell-v1"
SECRET_NAMES = {
    *ALL_CREDENTIAL_NAMES,
    "VOICE_BRIDGE_VOLC_ACCESS_KEY",
    "VOICE_BRIDGE_VOLC_SECRET_KEY",
    "VOICE_BRIDGE_VOLC_RTC_APP_ID",
    "VOICE_BRIDGE_VOLC_RTC_APP_KEY",
    "VOICE_BRIDGE_VOLC_SPEECH_APP_ID",
    "VOICE_BRIDGE_VOLC_SPEECH_ACCESS_TOKEN",
    "VOICE_BRIDGE_VOLC_SPEECH_API_KEY",
}
PACKAGE_METADATA_RESOURCE = "package-build.json"
BUILD_IDENTITY_RESOURCE = "build-identity.json"
INSTALL_PROFILE_RESOURCE = "install-profile.json"
XIAOHONGSHU_LICENSE_SHA256 = "9d8b162d4b4822b793666e2a8cb6c94df7ecdb8204f23a20e6042f93e691cb0d"
XIAOHONGSHU_BUILDER_PATH = REPO_ROOT / "scripts" / "_lib" / "xiaohongshu_connector" / "build.py"


def _load_xiaohongshu_builder() -> ModuleType:
    name = "agent_friend_package_xiaohongshu_connector_builder"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, XIAOHONGSHU_BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the Xiaohongshu Windows connector builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _windows_xiaohongshu_contract() -> tuple[ModuleType, str, str, tuple[str, ...]]:
    builder = _load_xiaohongshu_builder()
    lock = builder.load_windows_build_lock()
    source = lock["inputs"]["xiaohongshu_mcp"]
    filenames = tuple(item["filename"] for item in lock["outputs"])
    return builder, str(source["version"]), str(source["commit"]), filenames


@contextmanager
def _timed_step(name: str, build_paths: BuildPaths):  # type: ignore[no-untyped-def]
    started = time.monotonic()
    print(f"==> package step: {name}")
    try:
        yield
    finally:
        duration = time.monotonic() - started
        print(f"<== package step: {name} ({duration:.1f}s)")
        timing_path = build_paths.build_dir / "package-timings.json"
        try:
            timings = _read_json(timing_path) if timing_path.is_file() else {"steps": []}
            timings.setdefault("steps", []).append(
                {"name": name, "durationSeconds": round(duration, 3)}
            )
            timing_path.write_text(json.dumps(timings, indent=2) + "\n", encoding="utf-8")
        except Exception as error:
            print(f"warning: package timing record failed for {name}: {error}")


@contextmanager
def _package_build_lock(lock_path: Path):  # type: ignore[no-untyped-def]
    """Serialize builds that share caches or formal release-version allocation."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    acquired = False
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            raise RuntimeError(
                f"another package build already owns this governed build scope: {lock_path}"
            ) from error
        yield
    finally:
        try:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _sha256(path: Path) -> str:
    return sha256(path)


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


def _capture(command: list[str], *, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip() or result.stderr.strip()


def _source_metadata() -> dict[str, Any]:
    commit = _capture(["git", "rev-parse", "HEAD"])
    status = _capture(["git", "status", "--porcelain=v1", "-z"])
    dirty = bool(status)
    if not dirty:
        tree_sha256 = hashlib.sha256(commit.encode("ascii")).hexdigest()
    else:
        digest = hashlib.sha256()
        digest.update(
            subprocess.run(
                ["git", "diff", "--binary", "HEAD"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            ).stdout
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
        for raw_path in sorted(item for item in untracked if item):
            digest.update(b"untracked\0" + raw_path + b"\0")
            path = REPO_ROOT / os.fsdecode(raw_path)
            if path.is_file():
                digest.update(path.read_bytes())
        tree_sha256 = digest.hexdigest()
    return {"commit": commit, "dirty": dirty, "tree_sha256": tree_sha256}


def _repository_paths() -> set[str]:
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return {os.fsdecode(item) for item in output.split(b"\0") if item}


def _changed_repository_paths() -> set[str]:
    changed = set(
        line for line in _capture(["git", "diff", "--name-only", "HEAD"]).splitlines() if line
    )
    changed.update(
        line for line in _capture(["git", "diff", "--cached", "--name-only"]).splitlines() if line
    )
    changed.update(
        line
        for line in _capture(["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
        if line
    )
    try:
        merge_base = _capture(["git", "merge-base", "HEAD", "main"])
    except subprocess.CalledProcessError:
        merge_base = None
    if merge_base:
        changed.update(
            line
            for line in _capture(["git", "diff", "--name-only", merge_base, "HEAD"]).splitlines()
            if line
        )
    return changed


def _toolchain_versions(pnpm: str) -> dict[str, str]:
    frontend = _read_json(REPO_ROOT / "frontend" / "package.json")
    tauri_cli = str(frontend.get("devDependencies", {}).get("@tauri-apps/cli", "unknown"))
    return {
        "python": platform.python_version(),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "node": _capture(["node", "--version"]),
        "pnpm": _capture([pnpm, "--version"]),
        "rustc": _capture(["rustc", "--version"]),
        "cargo": _capture(["cargo", "--version"]),
        "tauri_cli": tauri_cli,
    }


def _installer_verification_inputs() -> InstallerVerificationInputs:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is required to compute installer verification inputs")
    environment = _toolchain_versions(pnpm)
    environment.update(
        {
            "platform_system": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "platform_machine": platform.machine(),
            "source_content_identity": "git-clean-filter-v1",
        }
    )
    return build_installer_verification_inputs(
        REPO_ROOT,
        repository_paths=_repository_paths(),
        build_environment=environment,
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_readiness(
    port: int,
    token: str,
    process: subprocess.Popen[bytes],
    *,
    service: str,
    expected_identity: BuildIdentity | None,
) -> None:
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
            if payload.get("status") == "ready" and expected_identity is None:
                return
            if payload.get("status") == "ready" and expected_identity is not None:
                identity_hash = hashlib.sha256(
                    canonical_json_bytes(expected_identity.to_json_dict())
                ).hexdigest()
                expected = {
                    "service": service,
                    "buildId": expected_identity.build_id,
                    "profileId": expected_identity.profile_id,
                    "buildIdentitySha256": identity_hash,
                }
                observed = {key: payload.get(key) for key in expected}
                if observed != expected:
                    raise RuntimeError(
                        f"frozen readiness identity mismatch: expected {expected}, got {observed}"
                    )
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


def _smoke_frozen_services(
    launcher: Path,
    base_env: dict[str, str],
    *,
    expected_identity: BuildIdentity | None = None,
) -> None:
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
                    _wait_for_readiness(
                        port,
                        token,
                        process,
                        service=service,
                        expected_identity=expected_identity,
                    )
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
    return str(read_project_version(REPO_ROOT).version)


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


def _install_memory_model(destination: Path, *, cache_dir: Path) -> None:
    from memory_eval.retrieval_benchmark.runner import install_onnx_assets

    cached_model = cache_dir / f"memory-model-{MODEL_REVISION}"
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


def _build_runtime(
    build_paths: BuildPaths,
    *,
    identity: BuildIdentity,
    offline: bool,
    reuse_frozen: bool,
) -> None:
    validate_frozen_runtime_dependency_policy(REPO_ROOT)
    work_dir = build_paths.pyinstaller_dir
    dist_dir = work_dir / "dist"
    frozen_launcher = dist_dir / "agent-friend-runtime" / "agent-friend-runtime.exe"
    if reuse_frozen:
        if not frozen_launcher.is_file():
            raise RuntimeError("--reuse-frozen requested but no previous frozen runtime exists")
    else:
        if work_dir.exists():
            safe_rmtree(work_dir, build_paths=build_paths)
        work_dir.mkdir(parents=True)
        build_paths.pyinstaller_cache_dir.mkdir(parents=True, exist_ok=True)
        identity_module = _write_runtime_identity_module(build_paths, identity)
        pyinstaller_env = _sanitized_env()
        pyinstaller_env["AGENT_FRIEND_RUNTIME_IDENTITY_MODULE"] = str(identity_module)
        with _timed_step("pyinstaller", build_paths):
            _run(
                [
                    sys.executable,
                    "-m",
                    "PyInstaller",
                    "--noconfirm",
                    "--distpath",
                    str(dist_dir),
                    "--workpath",
                    str(build_paths.pyinstaller_cache_dir),
                    str(SPEC_PATH),
                ],
                env=pyinstaller_env,
            )

    with _timed_step("runtime-stage-and-smoke", build_paths):
        _stage_runtime(build_paths, dist_dir=dist_dir, offline=offline, identity=identity)
        _stage_payload(build_paths, identity)


def _stage_runtime(
    build_paths: BuildPaths,
    *,
    dist_dir: Path,
    offline: bool,
    identity: BuildIdentity,
) -> None:
    stage_dir = build_paths.runtime_stage
    if stage_dir.exists():
        safe_rmtree(stage_dir, build_paths=build_paths)
    stage_dir.mkdir(parents=True)
    shutil.copytree(dist_dir / "agent-friend-runtime", stage_dir / "python")
    _copy_runtime_supplements(stage_dir / "python")

    cloudflared_lock = _read_json(CLOUDFLARED_LOCK)
    cache_dir = build_paths.roots.cache_dir
    cloudflared_cache = cache_dir / f"cloudflared-{cloudflared_lock['version']}-windows-amd64.exe"
    cloudflared = _download_locked(CLOUDFLARED_LOCK, cloudflared_cache, offline=offline)
    vendor_dir = stage_dir / "vendor"
    vendor_dir.mkdir()
    shutil.copy2(cloudflared, vendor_dir / "cloudflared.exe")
    shutil.copy2(CLOUDFLARED_LOCK, vendor_dir / "cloudflared.lock.json")
    cloudflared_license = _download(
        url=str(cloudflared_lock["license_url"]),
        expected_sha256=str(cloudflared_lock["license_sha256"]),
        destination=cache_dir / f"cloudflared-{cloudflared_lock['version']}-LICENSE",
        offline=offline,
    )
    shutil.copy2(cloudflared_license, vendor_dir / "LICENSE-cloudflared.txt")

    xhs_dir = stage_dir / "connectors" / "xiaohongshu"
    xhs_dir.mkdir(parents=True)
    builder, xhs_version, xhs_commit, _ = _windows_xiaohongshu_contract()
    builder.ensure_windows_xiaohongshu_artifacts(
        xhs_dir,
        cache_dir=cache_dir / "xiaohongshu-windows-source-build",
        offline=offline,
    )
    shutil.copy2(builder.LOCK_PATH, xhs_dir / "BUILD-LOCK.json")
    shutil.copy2(builder.PATCH_PATH, xhs_dir / "COMPATIBILITY.patch")
    xhs_license = _download(
        url=(f"https://raw.githubusercontent.com/xpzouying/xiaohongshu-mcp/{xhs_commit}/LICENSE"),
        expected_sha256=XIAOHONGSHU_LICENSE_SHA256,
        destination=cache_dir / f"xiaohongshu-mcp-{xhs_version}-LICENSE",
        offline=offline,
    )
    shutil.copy2(xhs_license, xhs_dir / "LICENSE.txt")

    _install_memory_model(stage_dir / MODEL_RELATIVE_DIR, cache_dir=cache_dir)
    (stage_dir / "THIRD-PARTY-NOTICES.txt").write_text(
        "cloudflared\n"
        f"Version: {cloudflared_lock['version']}\n"
        f"Source: {cloudflared_lock['source']}\n"
        "License: Apache License 2.0 (see vendor/LICENSE-cloudflared.txt)\n\n"
        "xiaohongshu-mcp\n"
        f"Version: {xhs_version}\n"
        "Source: https://github.com/xpzouying/xiaohongshu-mcp\n"
        "Windows compatibility: reproducible source build; see "
        "connectors/xiaohongshu/BUILD-LOCK.json and COMPATIBILITY.patch\n"
        "License: Apache License 2.0 (see connectors/xiaohongshu/LICENSE.txt)\n",
        encoding="utf-8",
    )

    doctor_env = _sanitized_env()
    doctor_env["AGENT_FRIEND_MEMORY_MODEL_DIR"] = str(stage_dir / MODEL_RELATIVE_DIR)
    staged_launcher = stage_dir / "python" / "agent-friend-runtime.exe"
    _run([str(staged_launcher), "doctor"], env=doctor_env)
    _smoke_frozen_services(staged_launcher, doctor_env, expected_identity=identity)
    _write_runtime_inventory(stage_dir)


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


def _trusted_resource_paths(build_paths: BuildPaths) -> tuple[Path, Path]:
    directory = build_paths.generated_config_dir / "trusted-resources"
    return directory / "trusted-credential-seed.bin", directory / "trusted-credential-seed.key"


def _package_metadata_path(build_paths: BuildPaths) -> Path:
    return build_paths.generated_config_dir / PACKAGE_METADATA_RESOURCE


def _build_identity_path(build_paths: BuildPaths) -> Path:
    return build_paths.generated_config_dir / BUILD_IDENTITY_RESOURCE


def _install_profile_path(build_paths: BuildPaths) -> Path:
    return build_paths.generated_config_dir / INSTALL_PROFILE_RESOURCE


def _prepare_build_contract(
    build_paths: BuildPaths,
    *,
    product_version: str,
    source: dict[str, Any],
    local_test: bool,
    trusted_beta: bool,
    profile: InstallProfile,
    reuse: bool,
) -> BuildIdentity:
    identity_path = _build_identity_path(build_paths)
    profile_path = _install_profile_path(build_paths)
    expected = build_identity(
        product_version=product_version,
        build_id=build_paths.build_id,
        source=source,
        local_test=local_test,
        trusted_beta=trusted_beta,
        profile=profile,
    )
    if reuse:
        actual = read_build_identity(identity_path)
        if actual != expected:
            raise RuntimeError(
                "reused package build identity does not match the current source, version, "
                "distribution, or install profile; rebuild the runtime"
            )
        try:
            existing_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"could not read reused install profile: {profile_path}") from error
        if existing_profile != profile.to_json_dict():
            raise RuntimeError(
                "reused package install profile does not match the requested profile"
            )
        return actual
    write_canonical_json(identity_path, expected.to_json_dict())
    write_canonical_json(profile_path, profile.to_json_dict())
    return expected


def _write_runtime_identity_module(build_paths: BuildPaths, identity: BuildIdentity) -> Path:
    path = build_paths.generated_config_dir / "agent_friend_build_identity.py"
    identity_json = canonical_json_bytes(identity.to_json_dict()).decode("utf-8").strip()
    path.write_text(
        "# Generated by scripts/package/run.py; embedded into the frozen runtime.\n"
        f"BUILD_IDENTITY_JSON = {identity_json!r}\n",
        encoding="utf-8",
    )
    return path


def _stage_payload(build_paths: BuildPaths, identity: BuildIdentity) -> None:
    stage = build_paths.payload_stage
    if stage.exists():
        safe_rmtree(stage, build_paths=build_paths)
    stage.mkdir(parents=True)
    shutil.copytree(build_paths.runtime_stage, stage / "runtime")
    avatar_seed = stage / "avatar-seed"
    shutil.copytree(
        REPO_ROOT / "frontend" / "public" / "live2d-models" / "hiyori",
        avatar_seed / "live2d" / "hiyori",
    )
    shutil.copytree(
        REPO_ROOT / "frontend" / "public" / "spine-models" / "ark-models",
        avatar_seed / "spine" / "ark-models",
    )
    write_payload_manifest(stage, identity)


def _tauri_relative_path(path: Path) -> str:
    """Return a config path relative to src-tauri, as required by frontendDist/resources."""

    try:
        relative = Path(os.path.relpath(path.resolve(), TAURI_DIR.resolve()))
    except ValueError as error:
        raise RuntimeError(
            f"package build directory must be on the same drive as the repository: {path}"
        ) from error
    return relative.as_posix()


def _nsis_path(path: Path) -> str:
    return str(path.resolve())


def _apply_nsis_template_overlay(
    source: str,
    *,
    helper_path: Path,
    identity: BuildIdentity,
    profile: InstallProfile,
    trusted_resources: tuple[Path, Path] | None,
) -> str:
    hooks_anchor = """{{#if installer_hooks}}\n!include \"{{installer_hooks}}\"\n{{/if}}\n"""
    contract_defines = (
        f'!define AGENT_FRIEND_BUILD_ID "{identity.build_id}"\n'
        f'!define AGENT_FRIEND_PROFILE_ID "{profile.profile_id}"\n'
        f'!define AGENT_FRIEND_PRODUCT_NAME "{profile.product_name}"\n'
        f'!define AGENT_FRIEND_PRODUCT_VERSION "{identity.product_version}"\n'
        f'!define AGENT_FRIEND_PAYLOAD_RELATIVE "{payload_install_relative_path(identity.build_id)}"\n'
        f'!define AGENT_FRIEND_AUTOSTART_VALUE "{profile.autostart_value_name}"\n'
        '!define AGENT_FRIEND_INSTALLER_HELPER "agent-friend-installer-helper.exe"\n\n'
    )
    language_anchor = """  !if \"${DISPLAYLANGUAGESELECTOR}\" == \"true\"\n    !insertmacro MUI_LANGDLL_DISPLAY\n  !endif\n"""
    language_overlay = (
        """  ; agent-friend overlay: use Simplified Chinese unless this product identity\n  ; has a previously saved explicit language selection.\n  ClearErrors\n  ReadRegStr $R9 HKCU \"${MANUPRODUCTKEY}\" \"Installer Language\"\n  ${If} ${Errors}\n    StrCpy $LANGUAGE ${LANG_SIMPCHINESE}\n  ${EndIf}\n\n  ; Tauri's registry-backed MUI macro otherwise skips the selector whenever\n  ; an earlier install saved a language. Interactive installs must always let\n  ; the user confirm or change that choice; NSIS still suppresses it for /S.\n  !define MUI_LANGDLL_ALWAYSSHOW\n"""
        + language_anchor
    )
    upgrade_anchor = """  nsis_tauri_utils::SemverCompare \"${VERSION}\" $R0\n  Pop $R0\n"""
    upgrade_overlay = (
        upgrade_anchor
        + """  ; agent-friend overlay: upgrades are handled by the transactional\n  ; install hook. Downgrades stop before the directory or install pages mutate state.\n  ${If} $R0 = 1\n  ${AndIf} $WixMode != 1\n    Abort\n  ${ElseIf} $R0 = -1\n  ${AndIf} $WixMode != 1\n    IfSilent agent_friend_downgrade_blocked_done 0\n    ${If} $PassiveMode != 1\n      MessageBox MB_OK|MB_ICONSTOP "$(AgentFriendDowngradeBlocked)"\n    ${EndIf}\n    agent_friend_downgrade_blocked_done:\n    DetailPrint "${PRODUCTNAME}: $(AgentFriendDowngradeBlocked)"\n    SetErrorLevel 35\n    Quit\n  ${EndIf}\n"""
    )
    repair_label_anchor = '    StrCpy $R2 "$(addOrReinstall)"\n'
    repair_label_overlay = '    StrCpy $R2 "$(AgentFriendRepairOrReinstall)"\n'
    uninstall_only_anchor = """      MessageBox MB_ICONEXCLAMATION "$(unableToUninstall)"\n      Abort\n    ${EndIf}\n  reinst_done:\n"""
    uninstall_only_overlay = """      MessageBox MB_ICONEXCLAMATION "$(unableToUninstall)"\n      Abort\n    ${EndIf}\n    ; Same-version uninstall is a terminal maintenance action, not a prelude\n    ; to installing another copy.\n    ${If} $R0 = 0\n      SetErrorLevel 0\n      Quit\n    ${EndIf}\n  reinst_done:\n"""
    directory_anchor = (
        """!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n!insertmacro MUI_PAGE_DIRECTORY\n"""
    )
    directory_overlay = """Function AgentFriendConfirmInstallRoot\n  !insertmacro AGENT_FRIEND_RESOLVE_INSTALL_ROOT_INTENT\n  ${If} $AgentFriendInstallRootMove != "1"\n    Return\n  ${EndIf}\n  ${If} $AgentFriendMoveIntent == "1"\n    Return\n  ${EndIf}\n  StrCpy $0 "$AgentFriendRegisteredRoot"\n  StrCpy $1 "$INSTDIR"\n  MessageBox MB_YESNO|MB_ICONQUESTION "$(AgentFriendMoveInstallRoot)" IDYES agent_friend_move_confirmed\n  Abort\n  agent_friend_move_confirmed:\n    StrCpy $AgentFriendMoveIntent "1"\nFunctionEnd\n\n!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n!define MUI_PAGE_CUSTOMFUNCTION_LEAVE AgentFriendConfirmInstallRoot\n!insertmacro MUI_PAGE_DIRECTORY\n"""
    section_anchor = """Section Install\n  SetOutPath $INSTDIR\n"""
    plugin_files = (
        section_anchor
        + '  SetOutPath "$PLUGINSDIR"\n'
        + f'  File /oname=agent-friend-installer-helper.exe "{_nsis_path(helper_path)}"\n'
    )
    if trusted_resources is not None:
        payload, key = trusted_resources
        plugin_files += (
            f'  File /oname=trusted-credential-seed.bin "{_nsis_path(payload)}"\n'
            f'  File /oname=trusted-credential-seed.key "{_nsis_path(key)}"\n'
        )
    plugin_files += '  SetOutPath "$INSTDIR"\n\n'
    uninstall_guard_anchor = """  !ifmacrodef NSIS_HOOK_PREUNINSTALL
    !insertmacro NSIS_HOOK_PREUNINSTALL
  !endif

  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
"""
    uninstall_guard_overlay = """  !ifmacrodef NSIS_HOOK_PREUNINSTALL
    !insertmacro NSIS_HOOK_PREUNINSTALL
  !else
    !error "agent-friend requires its fail-closed uninstall guard"
  !endif
"""
    for name, anchor in (
        ("hooks", hooks_anchor),
        ("language", language_anchor),
        ("upgrade", upgrade_anchor),
        ("repair label", repair_label_anchor),
        ("uninstall-only maintenance", uninstall_only_anchor),
        ("directory leave", directory_anchor),
        ("install section", section_anchor),
        ("uninstall guard", uninstall_guard_anchor),
    ):
        if source.count(anchor) != 1:
            raise RuntimeError(f"pinned Tauri NSIS template {name} anchor drifted")
    return (
        source.replace(hooks_anchor, contract_defines + hooks_anchor)
        .replace(language_anchor, language_overlay)
        .replace(upgrade_anchor, upgrade_overlay)
        .replace(repair_label_anchor, repair_label_overlay)
        .replace(uninstall_only_anchor, uninstall_only_overlay)
        .replace(directory_anchor, directory_overlay)
        .replace(section_anchor, plugin_files)
        .replace(uninstall_guard_anchor, uninstall_guard_overlay)
    )


def _generate_nsis_template(
    build_paths: BuildPaths,
    *,
    offline: bool,
    helper_path: Path,
    identity: BuildIdentity,
    profile: InstallProfile,
    trusted_resources: tuple[Path, Path] | None,
) -> Path:
    cached = _download(
        url=TAURI_NSIS_TEMPLATE_URL,
        expected_sha256=TAURI_NSIS_TEMPLATE_SHA256,
        destination=build_paths.roots.cache_dir / "tauri-2.11.2-installer.nsi",
        offline=offline,
    )
    generated = _apply_nsis_template_overlay(
        cached.read_text(encoding="utf-8"),
        helper_path=helper_path,
        identity=identity,
        profile=profile,
        trusted_resources=trusted_resources,
    )
    output = build_paths.generated_config_dir / "installer.generated.nsi"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated, encoding="utf-8")
    return output


def _cleanup_trusted_resources(build_paths: BuildPaths) -> None:
    payload, key = _trusted_resource_paths(build_paths)
    for path in (payload, key):
        path.unlink(missing_ok=True)
    release_root = build_paths.cargo_target_dir / "release"
    if release_root.is_dir():
        sensitive_names = {payload.name, key.name}
        for path in release_root.rglob("*"):
            if path.is_file() and path.name in sensitive_names:
                path.unlink(missing_ok=True)


def _prepare_trusted_resources(
    build_paths: BuildPaths, *, credential_store: Any | None
) -> tuple[Path, Path]:
    _cleanup_trusted_resources(build_paths)
    credentials = collect_trusted_credentials(credential_store or KeyringSource())
    artifacts = encrypt_credential_seed(credentials)
    payload, key = _trusted_resource_paths(build_paths)
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(artifacts.payload)
    key.write_bytes(artifacts.key)
    return payload, key


def _clean_tauri_app_outputs(build_paths: BuildPaths) -> None:
    """Force fresh app/resource embedding while preserving compiled dependencies."""

    if build_paths.cargo_target_dir.exists():
        _run(
            [
                "cargo",
                "clean",
                "--package",
                "app",
                "--release",
                "--target-dir",
                str(build_paths.cargo_target_dir),
                "--manifest-path",
                str(TAURI_DIR / "Cargo.toml"),
            ],
            cwd=TAURI_DIR,
            env=_sanitized_env(),
        )
    bundle_dir = build_paths.cargo_target_dir / "release" / "bundle" / "nsis"
    if bundle_dir.exists():
        safe_cache_rmtree(bundle_dir, build_paths=build_paths)


def _build_installer_helper(build_paths: BuildPaths, *, env: dict[str, str]) -> Path:
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("cargo is required on the Windows build machine")
    with _timed_step("installer-helper", build_paths):
        _run(
            [
                cargo,
                "build",
                "--release",
                "--bin",
                "agent-friend-installer-helper",
                "--target-dir",
                str(build_paths.cargo_target_dir),
                "--manifest-path",
                str(TAURI_DIR / "Cargo.toml"),
            ],
            cwd=TAURI_DIR,
            env=env,
        )
    helper = build_paths.cargo_target_dir / "release" / "agent-friend-installer-helper.exe"
    if not helper.is_file():
        raise RuntimeError(f"installer helper build output is missing: {helper}")
    # Tauri's app-only Cargo build may remove sibling binary artifacts from the
    # shared target directory before NSIS runs. Stage a build-owned immutable copy.
    build_paths.generated_config_dir.mkdir(parents=True, exist_ok=True)
    staged = build_paths.generated_config_dir / helper.name
    shutil.copy2(helper, staged)
    return staged


def _write_package_config(
    build_paths: BuildPaths,
    *,
    identity: BuildIdentity,
    profile: InstallProfile,
    trusted_beta: bool,
    nsis_template: Path,
) -> Path:
    config = _read_json(TAURI_CONFIG)
    bundle = config.setdefault("bundle", {})
    resources = bundle.setdefault("resources", {})
    if not isinstance(resources, dict):
        raise RuntimeError("package Tauri resources must be an object")
    resources.clear()
    resources[build_paths.payload_stage.as_posix() + "/"] = payload_install_relative_path(
        identity.build_id
    )
    config["productName"] = profile.product_name
    config["mainBinaryName"] = profile.main_binary_name
    config["identifier"] = profile.bundle_identifier
    config["version"] = identity.product_version
    nsis = bundle.setdefault("windows", {}).setdefault("nsis", {})
    nsis["languages"] = ["SimpChinese", "English"]
    nsis["displayLanguageSelector"] = True
    nsis["template"] = _tauri_relative_path(nsis_template)
    build = config.setdefault("build", {})
    if not isinstance(build, dict):
        raise RuntimeError("package Tauri build config must be an object")
    build["beforeBuildCommand"] = ""
    build["frontendDist"] = _tauri_relative_path(build_paths.frontend_dist)
    build_paths.generated_config_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = _package_metadata_path(build_paths)
    metadata_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "buildMode": identity.build_mode,
                "distribution": identity.distribution,
                "releaseChannel": (
                    "local-test"
                    if identity.build_mode == "local-test"
                    else "trusted-beta"
                    if identity.distribution == "trusted-beta"
                    else "stable"
                ),
                "sensitive": trusted_beta,
                "buildId": identity.build_id,
                "profileId": identity.profile_id,
                "buildIdentitySha256": hashlib.sha256(
                    canonical_json_bytes(identity.to_json_dict())
                ).hexdigest(),
                "source": {
                    "commit": identity.source_commit,
                    "dirty": identity.dirty,
                    "tree_sha256": identity.source_tree_sha256,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    resources[metadata_path.as_posix()] = PACKAGE_METADATA_RESOURCE
    if trusted_beta:
        payload, key = _trusted_resource_paths(build_paths)
        if not payload.is_file() or not key.is_file():
            raise RuntimeError("trusted resources were not prepared for NSIS plugin staging")
    config_path = build_paths.generated_config_dir / "tauri.package.generated.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def _validate_frontend_dist(frontend_dist: Path) -> list[str]:
    if not frontend_dist.is_dir():
        raise RuntimeError(f"frontend build output is missing: {frontend_dist}")
    entrypoints = sorted(path.name for path in frontend_dist.glob("*.html"))
    missing = sorted(REQUIRED_FRONTEND_ENTRYPOINTS.difference(entrypoints))
    if missing:
        raise RuntimeError(
            "frontend build output is incomplete; missing entrypoints: " + ", ".join(missing)
        )
    return entrypoints


def _validate_embedded_frontend(
    executable: Path,
    frontend_dist: Path,
    entrypoints: list[str],
    cargo_target_dir: Path,
) -> None:
    _validate_desktop_shell_role(executable)
    expected_files = sorted(path for path in frontend_dist.rglob("*") if path.is_file())
    expected = {
        path.relative_to(frontend_dist).as_posix(): (
            path.suffix.lower(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in expected_files
    }
    frontend_mtime_ns = max(path.stat().st_mtime_ns for path in expected_files)
    executable_mtime_ns = executable.stat().st_mtime_ns
    codegen_roots = sorted(
        (cargo_target_dir / "release" / "build").glob("app-*/out/tauri-codegen-assets"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    best_missing = list(expected)
    for codegen_root in codegen_roots:
        assets = [path for path in codegen_root.iterdir() if path.is_file()]
        if not assets:
            continue
        newest_asset_mtime_ns = max(path.stat().st_mtime_ns for path in assets)
        if newest_asset_mtime_ns < frontend_mtime_ns or newest_asset_mtime_ns > executable_mtime_ns:
            continue
        observed: set[tuple[str, str]] = set()
        for path in assets:
            try:
                content = brotli.decompress(path.read_bytes())
            except brotli.error:
                continue
            key = (path.suffix.lower(), hashlib.sha256(content).hexdigest())
            observed.add(key)
        # Tauri deduplicates identical source files into one codegen asset, so
        # content membership is the contract; multiplicity is intentionally ignored.
        missing = [relative for relative, key in expected.items() if key not in observed]
        if not missing:
            return
        if len(missing) < len(best_missing):
            best_missing = missing
    missing_entrypoints = [name for name in entrypoints if name in best_missing]
    detail = missing_entrypoints or best_missing[:10]
    raise RuntimeError(
        "built Tauri executable does not contain the complete frontend according to "
        "its Brotli codegen assets; refusing to publish. Missing embedded files: "
        + ", ".join(detail)
    )


def _validate_desktop_shell_role(executable: Path) -> None:
    if not executable.is_file():
        raise RuntimeError(f"built Tauri executable is missing: {executable}")
    if DESKTOP_SHELL_ROLE_MARKER not in executable.read_bytes():
        raise RuntimeError(
            "built Tauri executable role mismatch; the selected binary is not the "
            "desktop shell. Check Cargo default-run and Tauri binary selection"
        )


def _build_installer(
    version: str,
    *,
    build_paths: BuildPaths,
    identity: BuildIdentity,
    profile: InstallProfile,
    verification_inputs: InstallerVerificationInputs,
    local_test: bool,
    trusted_beta: bool = False,
    offline: bool = False,
    credential_store: Any | None = None,
) -> Path:
    if not (build_paths.payload_stage / "payload-manifest.json").is_file():
        raise RuntimeError("versioned payload stage missing; run the runtime step first")
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is required on the Windows build machine")
    bundle_dir = build_paths.cargo_target_dir / "release" / "bundle" / "nsis"
    candidate: Path | None = None
    try:
        build_env = _sanitized_env()
        build_env["CARGO_TARGET_DIR"] = str(build_paths.cargo_target_dir)
        build_env["AGENT_FRIEND_BUILD_IDENTITY_JSON"] = (
            canonical_json_bytes(identity.to_json_dict()).decode("utf-8").strip()
        )
        build_env["AGENT_FRIEND_PRODUCT_VERSION"] = identity.product_version
        build_env["AGENT_FRIEND_INSTALL_PROFILE_JSON"] = (
            canonical_json_bytes(profile.to_json_dict()).decode("utf-8").strip()
        )
        if build_paths.frontend_dist.exists():
            safe_rmtree(build_paths.frontend_dist, build_paths=build_paths)
        with _timed_step("frontend", build_paths):
            _run(
                [
                    pnpm,
                    "run",
                    "build",
                    "--outDir",
                    str(build_paths.frontend_dist),
                    "--emptyOutDir",
                ],
                cwd=REPO_ROOT / "frontend",
                env=build_env,
            )
        entrypoints = _validate_frontend_dist(build_paths.frontend_dist)
        # Recompile the app/resource layer on every package while retaining Cargo dependency
        # artifacts in the per-worktree cache. This prevents stale frontend or trusted-resource
        # embedding without paying for a full dependency rebuild.
        _clean_tauri_app_outputs(build_paths)
        helper = _build_installer_helper(build_paths, env=build_env)
        trusted_resources = (
            _prepare_trusted_resources(build_paths, credential_store=credential_store)
            if trusted_beta
            else None
        )
        nsis_template = _generate_nsis_template(
            build_paths,
            offline=offline,
            helper_path=helper,
            identity=identity,
            profile=profile,
            trusted_resources=trusted_resources,
        )
        config_path = _write_package_config(
            build_paths,
            identity=identity,
            profile=profile,
            trusted_beta=trusted_beta,
            nsis_template=nsis_template,
        )
        with _timed_step("tauri-and-nsis", build_paths):
            _run(
                [pnpm, "tauri", "build", "--config", str(config_path), "--bundles", "nsis"],
                cwd=REPO_ROOT / "frontend",
                env=build_env,
            )
        _validate_embedded_frontend(
            build_paths.cargo_target_dir / "release" / f"{profile.main_binary_name}.exe",
            build_paths.frontend_dist,
            entrypoints,
            build_paths.cargo_target_dir,
        )
        candidates = sorted(bundle_dir.glob("*.exe"), key=lambda path: path.stat().st_mtime_ns)
        if not candidates:
            raise RuntimeError(f"new NSIS installer not found in {bundle_dir}")
        candidate = candidates[-1]
        distribution = "trusted-beta" if trusted_beta else "ordinary"
        manifest = {
            "schema_version": 2,
            "product": profile.product_name,
            "version": version,
            "platform": "windows-x64",
            "build_mode": "local-test" if local_test else "formal",
            "publishable": not local_test,
            "build_id": identity.build_id,
            "profile_id": profile.profile_id,
            "created_at": datetime.now(UTC).isoformat(),
            "distribution": distribution,
            "release_channel": (
                "local-test" if local_test else "trusted-beta" if trusted_beta else "stable"
            ),
            "sensitive": trusted_beta,
            "source": {
                "commit": identity.source_commit,
                "dirty": identity.dirty,
                "tree_sha256": identity.source_tree_sha256,
            },
            "toolchain": _toolchain_versions(pnpm),
            "installer_verification": verification_inputs.to_json_dict(),
            "installer": {},
            "upgrade_mode": "manual-installer",
            "identifier": profile.bundle_identifier,
        }
        if trusted_beta and not local_test:
            parsed_version = SemVer.parse(version)
            manifest["target_release_version"] = (
                f"{parsed_version.major}.{parsed_version.minor}.{parsed_version.patch}"
            )
        with _timed_step("publish", build_paths):
            return publish_installer(
                candidate,
                releases_root=build_paths.roots.releases_root,
                manifest=manifest,
                local_test=local_test,
            )
    finally:
        if trusted_beta:
            _cleanup_trusted_resources(build_paths)
            if candidate is not None:
                candidate.unlink(missing_ok=True)
            _clean_tauri_app_outputs(build_paths)


def _smoke_installed_runtime(
    install_dir: Path,
    *,
    profile: InstallProfile = PRODUCTION_PROFILE,
    expect_trusted_credentials: bool = False,
) -> None:
    install_root = install_dir.resolve()
    receipt_path = install_root / "active-install.json"
    if not receipt_path.is_file():
        raise RuntimeError(f"installed runtime is incomplete: {receipt_path}")
    receipt = _read_json(receipt_path)
    if receipt.get("schemaVersion") != 1 or receipt.get("profileId") != profile.profile_id:
        raise RuntimeError("installed runtime receipt does not match the requested profile")
    relative = safe_relative_path(str(receipt.get("payloadRelativePath", "")))
    runtime_payload = (install_root / Path(relative)).resolve()
    try:
        runtime_payload.relative_to(install_root)
    except ValueError as error:
        raise RuntimeError("installed runtime payload escaped its install root") from error
    identity = read_build_identity(runtime_payload / "build-identity.json")
    if identity.build_id != receipt.get("buildId") or identity.profile_id != profile.profile_id:
        raise RuntimeError("installed runtime build identity does not match its active receipt")
    manifest = read_payload_manifest(runtime_payload / "payload-manifest.json")
    validate_payload_manifest(runtime_payload, manifest)
    if manifest.build_identity_sha256 != receipt.get("buildIdentitySha256"):
        raise RuntimeError("installed runtime manifest identity does not match its active receipt")
    if _sha256(runtime_payload / "payload-manifest.json") != receipt.get("payloadManifestSha256"):
        raise RuntimeError("installed runtime manifest hash does not match its active receipt")

    main_binary = install_root / f"{profile.main_binary_name}.exe"
    runtime_dir = runtime_payload / "runtime"
    launcher = runtime_dir / "python" / "agent-friend-runtime.exe"
    inventory = runtime_dir / "runtime-inventory.json"
    model_dir = runtime_dir / MODEL_RELATIVE_DIR
    xhs_dir = runtime_dir / "connectors" / "xiaohongshu"
    _, _, _, xhs_filenames = _windows_xiaohongshu_contract()
    required = (
        main_binary,
        launcher,
        inventory,
        model_dir,
        *(xhs_dir / filename for filename in xhs_filenames),
        xhs_dir / "LICENSE.txt",
        xhs_dir / "BUILD-LOCK.json",
        xhs_dir / "COMPATIBILITY.patch",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        formatted = ", ".join(str(path) for path in missing)
        raise RuntimeError(f"installed runtime is incomplete: {formatted}")

    smoke_env = _sanitized_env()
    smoke_env["AGENT_FRIEND_MEMORY_MODEL_DIR"] = str(model_dir)
    smoke_env["AGENT_FRIEND_KEYRING_SERVICE"] = profile.keyring_service
    smoke_env["AGENT_FRIEND_SETTINGS_BUNDLE_ID"] = profile.settings_namespace
    if profile.profile_id == "installer-smoke":
        roaming = Path(os.environ["APPDATA"])
        local = Path(os.environ["LOCALAPPDATA"])
        smoke_env["AGENT_FRIEND_DATA_DIR"] = str(roaming / profile.data_namespace)
        smoke_env["AGENT_FRIEND_LOG_DIR"] = str(local / profile.log_namespace / "Logs")
    identity_result = subprocess.run(
        [str(launcher), "build-identity"],
        cwd=launcher.parent,
        env=smoke_env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    observed_identity = json.loads(identity_result.stdout)
    if observed_identity.get("identity") != identity.to_json_dict():
        raise RuntimeError("installed frozen runtime embedded identity mismatch")
    _run([str(launcher), "doctor"], cwd=launcher.parent, env=smoke_env)
    if expect_trusted_credentials:
        _run(
            [str(launcher), "credential-status", "--require-operational"],
            cwd=launcher.parent,
            env=smoke_env,
        )
    _smoke_frozen_services(launcher, smoke_env, expected_identity=identity)
    print(f"installed runtime smoke passed: {install_dir.resolve()}")


def _resolve_roots(args: argparse.Namespace) -> PackageRoots:
    return resolve_package_roots(
        REPO_ROOT,
        state_dir=args.state_dir,
        releases_dir=args.releases_dir,
    )


def _latest_stable_release(records: list[ReleaseRecord]) -> SemVer | None:
    stable = [
        record.version
        for record in records
        if record.distribution == "ordinary"
        and not record.version.prerelease
        and not record.version.build
    ]
    return max(stable) if stable else None


def _stable_release_floor(project: ProjectVersion, records: list[ReleaseRecord]) -> SemVer:
    latest = _latest_stable_release(records)
    return max(project.version, latest) if latest is not None else project.version


def _trusted_beta_candidate(
    roots: PackageRoots,
    project: ProjectVersion,
    records: list[ReleaseRecord],
) -> tuple[SemVer, SemVer]:
    channel = read_trusted_beta_channel(roots.releases_root)
    if channel is None:
        raise RuntimeError(
            "trusted-beta channel is not configured; run "
            "scripts/package/run.* trusted-beta-configure <next-stable-semver> once"
        )
    stable_floor = _stable_release_floor(project, records)
    if channel.target_release_version <= stable_floor:
        raise RuntimeError(
            "trusted-beta target release version is stale; configure a version newer than "
            f"the current/latest stable release ({stable_floor})"
        )
    candidate = next_trusted_beta_version(
        channel,
        (record.version for record in records if record.distribution == "trusted-beta"),
    )
    latest = latest_formal_version(records)
    if latest is not None and candidate <= latest:
        raise RuntimeError(
            f"next trusted-beta version {candidate} is not newer than latest formal "
            f"artifact {latest}; configure a newer target release"
        )
    return channel.target_release_version, candidate


def _release_status(roots: PackageRoots, project: ProjectVersion) -> dict[str, Any]:
    source = _source_metadata()
    records, warnings = discover_formal_releases(roots.releases_root)
    latest = latest_formal_version(records)
    version = str(project.version)
    stable_destination = formal_release_dir(roots.releases_root, version, "ordinary")
    stable_allowed = (
        not source["dirty"]
        and not project.version.prerelease
        and not project.version.build
        and not stable_destination.exists()
        and (latest is None or project.version >= latest)
    )
    trusted_status: dict[str, Any] = {
        "configuration": str(trusted_beta_channel_path(roots.releases_root)),
        "target_release_version": None,
        "next_package_version": None,
        "destination": None,
        "exists": False,
        "allowed": False,
        "reason": "trusted-beta channel is not configured",
    }
    try:
        target, candidate = _trusted_beta_candidate(roots, project, records)
        destination = formal_release_dir(roots.releases_root, str(candidate), "trusted-beta")
        trusted_status.update(
            {
                "target_release_version": str(target),
                "next_package_version": str(candidate),
                "destination": str(destination),
                "exists": destination.exists(),
                "allowed": not source["dirty"] and not destination.exists(),
                "reason": (
                    None
                    if not source["dirty"] and not destination.exists()
                    else "formal trusted-beta builds require a clean source and unused version"
                ),
            }
        )
    except RuntimeError as error:
        trusted_status["reason"] = str(error)
    return {
        "project_version": version,
        "version_sources": project.values,
        "latest_formal_version": str(latest) if latest else None,
        "source": source,
        "paths": {
            "state": str(roots.state_root),
            "cache": str(roots.cache_dir),
            "work": str(roots.work_root),
            "releases": str(roots.releases_root),
        },
        "formal": {
            "ordinary": {
                "version": version,
                "destination": str(stable_destination),
                "exists": stable_destination.exists(),
                "allowed": stable_allowed,
                "requires_explicit_release": True,
            },
            "trusted-beta": trusted_status,
        },
        "warnings": warnings,
    }


def _planned_package_version(
    roots: PackageRoots,
    project: ProjectVersion,
    *,
    local_test: bool,
    trusted_beta: bool,
) -> tuple[SemVer | None, SemVer]:
    if trusted_beta:
        records, warnings = discover_formal_releases(roots.releases_root)
        for warning in warnings:
            print(f"warning: invalid formal release ignored: {warning}")
        return _trusted_beta_candidate(roots, project, records)
    package_version = project.version
    if not local_test and (package_version.prerelease or package_version.build):
        raise RuntimeError(
            "stable --release builds require a project version without prerelease or build metadata"
        )
    return None, package_version


def _read_registry_value(key: Any, name: str) -> str:
    import winreg

    value, _ = winreg.QueryValueEx(key, name)
    if not isinstance(value, str) or not value.strip():
        label = name or "(default)"
        raise RuntimeError(f"production install registry value is missing: {label}")
    return value.strip()


def _read_current_production_install() -> dict[str, Any] | None:
    import winreg

    uninstall_path = (
        "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
        + PRODUCTION_PROFILE.registry_product_name
    )
    manufacturer_path = (
        f"Software\\{WINDOWS_INSTALLER_MANUFACTURER}\\" + PRODUCTION_PROFILE.registry_product_name
    )

    def open_optional(path: str) -> Any | None:
        try:
            return winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            return None

    uninstall = open_optional(uninstall_path)
    manufacturer = open_optional(manufacturer_path)
    if uninstall is None and manufacturer is None:
        return None
    if uninstall is None or manufacturer is None:
        if uninstall is not None:
            uninstall.Close()
        if manufacturer is not None:
            manufacturer.Close()
        raise RuntimeError(
            "production install identity is incomplete: uninstall and manufacturer "
            "registry records must either both exist or both be absent"
        )
    try:
        registered_version = _read_registry_value(uninstall, "DisplayVersion")
        uninstall_root = _read_registry_value(uninstall, "InstallLocation")
        manufacturer_root = _read_registry_value(manufacturer, "")
    finally:
        uninstall.Close()
        manufacturer.Close()
    if canonical_windows_install_root(uninstall_root) != canonical_windows_install_root(
        manufacturer_root
    ):
        raise RuntimeError("production install registry roots disagree")
    install_root = Path(uninstall_root.strip().strip('"'))
    receipt_path = install_root / ACTIVE_INSTALL_FILE
    receipt = read_active_install_receipt(receipt_path)
    if receipt.profile_id != PRODUCTION_PROFILE.profile_id:
        raise RuntimeError("active production install receipt has the wrong profile")
    expected_payload = payload_install_relative_path(receipt.build_id)
    if receipt.payload_relative_path != expected_payload:
        raise RuntimeError("active production install payload path disagrees with its build id")
    identity_path = install_root / Path(receipt.payload_relative_path) / "build-identity.json"
    identity = read_build_identity(identity_path)
    if identity.profile_id != PRODUCTION_PROFILE.profile_id:
        raise RuntimeError("active production build identity has the wrong profile")
    if identity.build_id != receipt.build_id:
        raise RuntimeError("active production receipt and build identity disagree on build id")
    if sha256(identity_path) != receipt.build_identity_sha256:
        raise RuntimeError("active production build identity hash mismatch")
    if identity.product_version != registered_version:
        raise RuntimeError(
            "production registry version and active build identity disagree: "
            f"{registered_version} != {identity.product_version}"
        )
    return {
        "version": registered_version,
        "installRoot": str(install_root.resolve(strict=False)),
        "buildId": identity.build_id,
        "sourceCommit": identity.source_commit,
        "sourceTreeSha256": identity.source_tree_sha256,
        "distribution": identity.distribution,
        "buildMode": identity.build_mode,
        "profileId": identity.profile_id,
    }


def _production_install_plan(candidate_version: SemVer) -> dict[str, Any]:
    installed = _read_current_production_install()
    state = classify_installed_version(
        None if installed is None else str(installed["version"]),
        str(candidate_version),
    )
    return {
        "schemaVersion": 1,
        "profileId": PRODUCTION_PROFILE.profile_id,
        "candidateVersion": str(candidate_version),
        "installed": installed,
        "state": state.value,
        "allowed": state != InstalledVersionState.DOWNGRADE_BLOCKED,
    }


def _require_install_candidate_allowed(plan: dict[str, Any]) -> None:
    if plan["state"] == InstalledVersionState.DOWNGRADE_BLOCKED.value:
        installed = plan["installed"]
        assert isinstance(installed, dict)
        raise RuntimeError(
            "production install plan rejected a downgrade before build: "
            f"installed={installed['version']} candidate={plan['candidateVersion']}"
        )


def _installer_contract_report(
    roots: PackageRoots,
    *,
    offline: bool,
    verification_inputs: InstallerVerificationInputs | None = None,
) -> dict[str, Any]:
    validate_profile_isolation()
    source = _download(
        url=TAURI_NSIS_TEMPLATE_URL,
        expected_sha256=TAURI_NSIS_TEMPLATE_SHA256,
        destination=roots.cache_dir / "tauri-2.11.2-installer.nsi",
        offline=offline,
    ).read_text(encoding="utf-8")
    identity = build_identity(
        product_version="0.0.0-contract",
        build_id="installer-contract-fixture",
        source={"commit": "0" * 40, "dirty": False, "tree_sha256": "1" * 64},
        local_test=True,
        trusted_beta=False,
        profile=resolve_install_profile("installer-smoke"),
    )
    generated = _apply_nsis_template_overlay(
        source,
        helper_path=roots.cache_dir / "contract-helper.exe",
        identity=identity,
        profile=resolve_install_profile("installer-smoke"),
        trusted_resources=None,
    )
    hooks = (SCRIPT_DIR / "windows" / "nsis-hooks.nsh").read_text(encoding="utf-8")
    language_pairs: dict[str, set[str]] = {}
    for line in hooks.splitlines():
        if line.startswith("LangString "):
            _, key, language, _ = line.split(" ", 3)
            language_pairs.setdefault(key, set()).add(language)
    expected_languages = {"2052", "1033"}
    incomplete = sorted(
        key for key, languages in language_pairs.items() if languages != expected_languages
    )
    if not language_pairs or incomplete:
        raise RuntimeError(f"installer localization contract is incomplete: {incomplete}")
    required_tokens = (
        "StrCpy $LANGUAGE ${LANG_SIMPCHINESE}",
        "!define MUI_LANGDLL_ALWAYSSHOW",
        "AGENT_FRIEND_INSTALLER_HELPER",
        "AGENT_FRIEND_PAYLOAD_RELATIVE",
        "${AndIf} $WixMode != 1",
    )
    missing = [token for token in required_tokens if token not in generated]
    if missing:
        raise RuntimeError(f"installer template overlay contract is incomplete: {missing}")
    current_inputs = verification_inputs or _installer_verification_inputs()
    return {
        "schemaVersion": 1,
        "status": "passed",
        "template": {
            "sha256": TAURI_NSIS_TEMPLATE_SHA256,
            "overlayAnchors": "passed",
        },
        "profiles": sorted(INSTALL_PROFILES),
        "localization": {
            "default": "SimpChinese",
            "selectable": ["SimpChinese", "English"],
            "interactiveSelector": "always",
            "savedLanguage": "preselected",
            "pairedMessageCount": len(language_pairs),
        },
        "impact": classify_installer_impact(_changed_repository_paths()),
        "verificationInputs": current_inputs.to_json_dict(),
    }


def _child_smoke_build(roots: PackageRoots, *, offline: bool) -> tuple[str, Path]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "all",
        "--local-test",
        "--retain-build-workspace",
        "--install-profile",
        "installer-smoke",
        "--state-dir",
        str(roots.state_root),
        "--releases-dir",
        str(roots.releases_root),
    ]
    if offline:
        command.append("--offline")
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=_sanitized_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    build_id = None
    installer = None
    assert process.stdout is not None
    for line in process.stdout:
        _write_console_text(line)
        if line.startswith("build_id: "):
            build_id = line.removeprefix("build_id: ").strip()
        elif line.startswith("installer: "):
            installer = Path(line.removeprefix("installer: ").strip())
    return_code = process.wait()
    if return_code != 0 or build_id is None or installer is None:
        raise RuntimeError(f"installer smoke child build failed with exit code {return_code}")
    return build_id, installer


def _write_console_text(value: str, *, stream: Any | None = None) -> None:
    """Forward UTF-8 child output without assuming a UTF-8 parent console."""
    target = stream or sys.stdout
    encoding = getattr(target, "encoding", None) or "utf-8"
    safe_value = value.encode(encoding, errors="replace").decode(encoding)
    target.write(safe_value)
    target.flush()


def _build_installer_smoke_pair(
    roots: PackageRoots,
    *,
    offline: bool,
    baseline_build_id: str | None = None,
) -> Path:
    candidate_build_id: str | None = None
    candidate_installer: Path | None = None
    if baseline_build_id is None:
        candidate_build_id, candidate_installer = _child_smoke_build(roots, offline=offline)
        baseline_build_id = candidate_build_id
        baseline_paths = create_build_paths(roots, build_id=baseline_build_id)
        shared_shell = (
            baseline_paths.cargo_target_dir
            / "release"
            / (f"{resolve_install_profile('installer-smoke').main_binary_name}.exe")
        )
        baseline_shell = baseline_paths.build_dir / "smoke-baseline-shell.exe"
        baseline_paths.build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_shell, baseline_shell)
    else:
        baseline_paths = create_build_paths(roots, build_id=baseline_build_id)
        baseline_shell = baseline_paths.build_dir / "smoke-baseline-shell.exe"
        identity = read_build_identity(baseline_paths.payload_stage / "build-identity.json")
        manifest = read_payload_manifest(baseline_paths.payload_stage / "payload-manifest.json")
        validate_payload_manifest(baseline_paths.payload_stage, manifest)
        if (
            identity.build_id != baseline_build_id
            or identity.profile_id != "installer-smoke"
            or manifest.profile_id != identity.profile_id
            or not baseline_shell.is_file()
            or not baseline_paths.runtime_stage.is_dir()
        ):
            raise RuntimeError("reused installer smoke baseline is incomplete or mismatched")
    _validate_desktop_shell_role(baseline_shell)
    if candidate_build_id is None:
        candidate_build_id, candidate_installer = _child_smoke_build(roots, offline=offline)
    assert candidate_installer is not None
    project = read_project_version(REPO_ROOT)
    return write_installer_smoke_build_report(
        roots,
        project_version=str(project.version),
        baseline_build_id=baseline_build_id,
        candidate_build_id=candidate_build_id,
        candidate_installer=candidate_installer,
    )


def _effective_package_verification_level(
    impact: dict[str, Any],
    *,
    requested: str | None,
    release: bool,
    smoke_report: Path | None,
) -> PackageVerificationLevel:
    minimum = PackageVerificationLevel(
        str(impact.get("minimumVerificationLevel", PackageVerificationLevel.STANDARD.value))
    )
    requested_level = (
        PackageVerificationLevel(requested)
        if requested is not None
        else PackageVerificationLevel.STANDARD
    )
    event_level = (
        PackageVerificationLevel.FULL_MATRIX
        if release or smoke_report is not None
        else PackageVerificationLevel.STANDARD
    )
    return max_package_verification_level(minimum, requested_level, event_level)


def _verify_installer_smoke_gate(
    roots: PackageRoots,
    *,
    step: str,
    profile: InstallProfile,
    effective_level: PackageVerificationLevel,
    smoke_report: Path | None,
    verification_inputs: InstallerVerificationInputs | None,
) -> dict[str, Any] | None:
    if step not in {"installer", "all"}:
        if smoke_report is not None:
            raise RuntimeError("--smoke-report is only valid for an installer build")
        return None
    if profile.profile_id != "production":
        if smoke_report is not None:
            raise RuntimeError("--smoke-report reuse is only supported for the production profile")
        return None
    if verification_inputs is None:
        raise RuntimeError("production installer verification inputs are missing")
    if effective_level == PackageVerificationLevel.FULL_MATRIX and smoke_report is None:
        raise RuntimeError(
            "full-matrix production builds require an explicit --smoke-report "
            "whose package input fingerprint matches the current build"
        )
    if smoke_report is None:
        return None
    return verify_smoke_report_for_reuse(
        smoke_report,
        roots=roots,
        expected_verification_inputs=verification_inputs,
    )


def _validate_formal_build(
    roots: PackageRoots,
    package_version: SemVer,
    *,
    source: dict[str, Any],
    distribution: str,
) -> None:
    if source["dirty"]:
        raise RuntimeError(
            "formal package builds require a clean worktree; use --local-test for local validation"
        )
    records, warnings = discover_formal_releases(roots.releases_root)
    for warning in warnings:
        print(f"warning: invalid formal release ignored: {warning}")
    latest = latest_formal_version(records)
    if latest is not None and package_version < latest:
        raise RuntimeError(
            f"package version {package_version} is lower than latest formal release {latest}"
        )
    destination = formal_release_dir(roots.releases_root, str(package_version), distribution)
    if destination.exists():
        raise RuntimeError(f"formal release already exists and is immutable: {destination}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "step",
        choices=(
            "runtime",
            "installer",
            "all",
            "installer-contract",
            "installer-smoke-build",
            "installer-smoke",
            "installer-smoke-verify",
            "installed-smoke",
            "workspace-status",
            "workspace-gc",
            "version-status",
            "production-install-plan",
            "version-set",
            "trusted-beta-configure",
        ),
        nargs="?",
        default="all",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="explicit SemVer used by version-set or trusted-beta-configure",
    )
    parser.add_argument(
        "--offline", action="store_true", help="require locked assets in local cache"
    )
    parser.add_argument(
        "--reuse-frozen",
        action="store_true",
        help="deprecated; use --reuse-build-id",
    )
    parser.add_argument(
        "--reuse-build-id",
        help="reuse a package build owned by this repository worktree",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        help="installed application directory used by the installed-smoke step",
    )
    parser.add_argument(
        "--trusted-beta",
        action="store_true",
        help=(
            "build a sensitive distributable beta using the configured automatic prerelease "
            "version (or combine with --local-test for a non-distributable credential test)"
        ),
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="explicitly build the stable ordinary release at the synchronized project version",
    )
    parser.add_argument(
        "--verification-level",
        choices=tuple(level.value for level in PackageVerificationLevel),
        help=(
            "upgrade package verification above the code-derived minimum; lower requests never "
            "downgrade the enforced level"
        ),
    )
    parser.add_argument(
        "--expect-trusted-credentials",
        action="store_true",
        help="require the installed-smoke target to have all operational credentials",
    )
    parser.add_argument(
        "--local-test",
        action="store_true",
        help="build a non-publishable local test installer and allow a dirty worktree",
    )
    parser.add_argument(
        "--for-current-install",
        action="store_true",
        help=(
            "fail before build unless the candidate can install over this machine's "
            "production identity"
        ),
    )
    parser.add_argument(
        "--smoke-build-report",
        type=Path,
        help="machine report produced by installer-smoke-build",
    )
    parser.add_argument(
        "--smoke-baseline-build-id",
        help="reuse a validated installer-smoke baseline while rebuilding the candidate",
    )
    parser.add_argument(
        "--smoke-report",
        type=Path,
        help="machine report produced by installer-smoke",
    )
    parser.add_argument(
        "--candidate-build-id",
        help="exact candidate build id required by installer-smoke-verify",
    )
    parser.add_argument(
        "--candidate-installer",
        type=Path,
        help="exact candidate installer required by installer-smoke-verify",
    )
    parser.add_argument(
        "--install-profile",
        choices=tuple(sorted(INSTALL_PROFILES)),
        default="production",
        help="select the governed Windows product identity (default: production)",
    )
    parser.add_argument("--state-dir", type=Path, help="override package cache/work root")
    parser.add_argument("--releases-dir", type=Path, help="override package releases root")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply workspace-gc; without this flag cleanup is a dry-run",
    )
    parser.add_argument(
        "--build-max-age-hours",
        type=float,
        default=48.0,
        help="workspace-gc retention for build-id directories (default: 48)",
    )
    parser.add_argument(
        "--cache-max-age-days",
        type=float,
        default=7.0,
        help="workspace-gc retention for inactive per-worktree caches (default: 7)",
    )
    parser.add_argument(
        "--retain-build-workspace",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def _validate_package_build_intent(
    *,
    step: str,
    profile: InstallProfile,
    local_test: bool,
    trusted_beta: bool,
    release: bool,
) -> None:
    if step not in {"runtime", "installer", "all"}:
        raise RuntimeError(f"package intent validation is not valid for {step}")
    if release and (local_test or trusted_beta):
        raise RuntimeError("--release cannot be combined with --local-test or --trusted-beta")
    if release and profile.profile_id != "production":
        raise RuntimeError("--release is only valid for the production install profile")
    if profile.profile_id == "installer-smoke":
        if not local_test:
            raise RuntimeError("installer-smoke profile requires --local-test")
        if trusted_beta:
            raise RuntimeError(
                "installer-smoke trusted-beta requires a governed dummy credential source"
            )
        return
    if not local_test and not trusted_beta and not release:
        raise RuntimeError(
            "package intent is required: choose --release for a stable public artifact, "
            "--trusted-beta for a sensitive trusted distribution, or --local-test for a "
            "non-distributable build"
        )
    if trusted_beta and step == "runtime":
        raise RuntimeError("--trusted-beta requires the installer or all step")


def main() -> int:
    args = _parse_args()
    validate_profile_isolation()
    profile = resolve_install_profile(args.install_profile)
    if args.for_current_install and (
        args.step not in {"all", "installer"} or profile.profile_id != "production"
    ):
        raise RuntimeError(
            "--for-current-install is only valid for production installer or all builds"
        )
    if args.verification_level is not None and args.step not in {"runtime", "installer", "all"}:
        raise RuntimeError("--verification-level is only valid for package build steps")
    if args.step == "version-set":
        if args.version is None:
            raise RuntimeError("version-set requires an explicit SemVer")
        if (
            args.local_test
            or args.trusted_beta
            or args.release
            or args.reuse_build_id
            or args.reuse_frozen
            or profile.profile_id != "production"
        ):
            raise RuntimeError("version-set does not accept package build mode options")
        result = set_project_version(REPO_ROOT, args.version)
        print(
            json.dumps({"project_version": str(result.version), "sources": result.values}, indent=2)
        )
        return 0
    if args.step == "trusted-beta-configure":
        if args.version is None:
            raise RuntimeError("trusted-beta-configure requires the next stable SemVer")
        if (
            args.local_test
            or args.trusted_beta
            or args.release
            or args.reuse_build_id
            or args.reuse_frozen
            or profile.profile_id != "production"
        ):
            raise RuntimeError("trusted-beta-configure does not accept package build mode options")
        roots = _resolve_roots(args)
        project = read_project_version(REPO_ROOT)
        records, warnings = discover_formal_releases(roots.releases_root)
        for warning in warnings:
            print(f"warning: invalid formal release ignored: {warning}")
        configured = configure_trusted_beta_channel(
            roots.releases_root,
            args.version,
            minimum_release_version=_stable_release_floor(project, records),
        )
        _, candidate = _trusted_beta_candidate(roots, project, records)
        print(
            json.dumps(
                {
                    "configuration": str(configured.path),
                    "target_release_version": str(configured.target_release_version),
                    "next_package_version": str(candidate),
                },
                indent=2,
            )
        )
        return 0
    if args.version is not None:
        raise RuntimeError(f"unexpected positional version for {args.step}: {args.version}")
    if args.smoke_baseline_build_id and args.step != "installer-smoke-build":
        raise RuntimeError("--smoke-baseline-build-id is only valid for installer-smoke-build")
    if args.smoke_report is not None and args.step not in {
        "all",
        "installer",
        "installer-smoke-verify",
    }:
        raise RuntimeError(
            "--smoke-report is only valid for a production installer build or "
            "installer-smoke-verify"
        )
    roots = _resolve_roots(args)
    if args.apply and args.step != "workspace-gc":
        raise RuntimeError("--apply is only valid for workspace-gc")
    if args.step not in {"workspace-status", "workspace-gc"} and (
        args.build_max_age_hours != 48.0 or args.cache_max_age_days != 7.0
    ):
        raise RuntimeError("workspace retention options require workspace-status or workspace-gc")
    if args.build_max_age_hours <= 0 or args.cache_max_age_days <= 0:
        raise RuntimeError("workspace retention values must be positive")
    if args.retain_build_workspace and not (
        args.step == "all" and args.local_test and profile.profile_id == "installer-smoke"
    ):
        raise RuntimeError("--retain-build-workspace is reserved for installer-smoke child builds")
    if args.step in {"workspace-status", "workspace-gc"}:
        if (
            args.local_test
            or args.trusted_beta
            or args.release
            or args.reuse_build_id
            or args.reuse_frozen
            or args.retain_build_workspace
            or profile.profile_id != "production"
        ):
            raise RuntimeError(f"{args.step} does not accept package build mode options")
        build_max_age = timedelta(hours=args.build_max_age_hours)
        cache_max_age = timedelta(days=args.cache_max_age_days)
        if args.step == "workspace-status" or not args.apply:
            report = workspace_inventory(
                roots,
                build_max_age=build_max_age,
                cache_max_age=cache_max_age,
            )
            report["mode"] = "status" if args.step == "workspace-status" else "dry-run"
            print(json.dumps(report, indent=2))
            return 0
        with _package_build_lock(package_workspace_lock_path(roots)):
            report = workspace_inventory(
                roots,
                build_max_age=build_max_age,
                cache_max_age=cache_max_age,
            )
            result = apply_workspace_gc(roots, report)
        print(json.dumps({"plan": report, "result": result, "mode": "applied"}, indent=2))
        return 0
    if args.step == "version-status":
        if (
            args.local_test
            or args.trusted_beta
            or args.release
            or args.reuse_build_id
            or args.reuse_frozen
            or profile.profile_id != "production"
        ):
            raise RuntimeError("version-status does not accept package build mode options")
        print(json.dumps(_release_status(roots, read_project_version(REPO_ROOT)), indent=2))
        return 0

    if args.step == "production-install-plan":
        if args.reuse_build_id or args.reuse_frozen or profile.profile_id != "production":
            raise RuntimeError(
                "production-install-plan does not accept build reuse or non-production profiles"
            )
        _validate_package_build_intent(
            step="all",
            profile=profile,
            local_test=args.local_test,
            trusted_beta=args.trusted_beta,
            release=args.release,
        )
        _ensure_windows_x64()
        target_release_version, package_version = _planned_package_version(
            roots,
            read_project_version(REPO_ROOT),
            local_test=args.local_test,
            trusted_beta=args.trusted_beta,
        )
        plan = _production_install_plan(package_version)
        if target_release_version is not None:
            plan["targetReleaseVersion"] = str(target_release_version)
        print(json.dumps(plan, indent=2))
        return 0

    if args.step == "installer-contract":
        if args.local_test or args.trusted_beta or args.release or args.reuse_build_id:
            raise RuntimeError("installer-contract does not accept package build mode options")
        print(json.dumps(_installer_contract_report(roots, offline=args.offline), indent=2))
        return 0

    _ensure_windows_x64()
    if args.step == "installer-smoke-build":
        if (
            args.local_test
            or args.trusted_beta
            or args.release
            or args.reuse_build_id
            or args.expect_trusted_credentials
        ):
            raise RuntimeError("installer-smoke-build only builds ordinary isolated artifacts")
        report = _build_installer_smoke_pair(
            roots,
            offline=args.offline,
            baseline_build_id=args.smoke_baseline_build_id,
        )
        print(f"installer_smoke_build_report: {report}")
        return 0
    if args.step == "installer-smoke":
        if args.local_test or args.trusted_beta or args.release or args.reuse_build_id:
            raise RuntimeError("installer-smoke does not accept package build mode options")
        if args.smoke_build_report is None:
            raise RuntimeError("installer-smoke requires --smoke-build-report")
        verification_inputs = _installer_verification_inputs()
        with _package_build_lock(package_workspace_lock_path(roots)):
            report = run_installer_smoke(
                roots,
                build_report_path=args.smoke_build_report,
                installed_smoke=_smoke_installed_runtime,
                expected_verification_inputs=verification_inputs,
            )
            try:
                deleted_builds = cleanup_smoke_builds_after_success(roots, args.smoke_build_report)
            except (OSError, RuntimeError) as error:
                deleted_builds = []
                print(f"warning: installer smoke workspace cleanup failed: {error}")
        print(f"installer_smoke_report: {report}")
        if deleted_builds:
            print("package_workspaces_removed: " + ",".join(deleted_builds))
        return 0
    if args.step == "installer-smoke-verify":
        if args.local_test or args.trusted_beta or args.release or args.reuse_build_id:
            raise RuntimeError("installer-smoke-verify does not accept package build mode options")
        if (
            args.smoke_report is None
            or args.candidate_build_id is None
            or args.candidate_installer is None
        ):
            raise RuntimeError(
                "installer-smoke-verify requires --smoke-report, --candidate-build-id, and --candidate-installer"
            )
        verify_smoke_report(
            args.smoke_report,
            candidate_build_id=args.candidate_build_id,
            candidate_installer=args.candidate_installer,
            expected_verification_inputs=_installer_verification_inputs(),
        )
        print("installer smoke report verified")
        return 0
    if args.step == "installed-smoke":
        if args.install_dir is None:
            raise RuntimeError("installed-smoke requires --install-dir")
        if args.local_test or args.release or args.reuse_build_id:
            raise RuntimeError("installed-smoke does not accept package build mode options")
        if args.trusted_beta:
            raise RuntimeError(
                "installed-smoke uses --expect-trusted-credentials, not --trusted-beta"
            )
        _smoke_installed_runtime(
            args.install_dir,
            profile=profile,
            expect_trusted_credentials=args.expect_trusted_credentials,
        )
        return 0
    if args.expect_trusted_credentials:
        raise RuntimeError("--expect-trusted-credentials requires installed-smoke")
    _validate_package_build_intent(
        step=args.step,
        profile=profile,
        local_test=args.local_test,
        trusted_beta=args.trusted_beta,
        release=args.release,
    )
    if args.reuse_frozen:
        raise RuntimeError("--reuse-frozen was replaced by --reuse-build-id")
    if args.step == "installer" and not args.reuse_build_id:
        raise RuntimeError("installer requires --reuse-build-id from a previous runtime build")
    verification_inputs = (
        _installer_verification_inputs() if args.step in {"installer", "all"} else None
    )
    impact = classify_installer_impact(_changed_repository_paths())
    effective_level = _effective_package_verification_level(
        impact,
        requested=args.verification_level,
        release=args.release,
        smoke_report=args.smoke_report,
    )
    print(f"package_required_by_code: {str(bool(impact['packageRequired'])).lower()}")
    print(f"minimum_verification_level: {impact['minimumVerificationLevel']}")
    print(f"effective_verification_level: {effective_level.value}")
    verified = _verify_installer_smoke_gate(
        roots,
        step=args.step,
        profile=profile,
        effective_level=effective_level,
        smoke_report=args.smoke_report,
        verification_inputs=verification_inputs,
    )
    if verified is not None:
        assert args.smoke_report is not None
        assert verification_inputs is not None
        print(
            "installer_smoke_evidence: "
            f"{args.smoke_report.resolve()} "
            f"candidate={verified['candidate']['buildId']} "
            f"package_input_sha256={verification_inputs.package_input_sha256}"
        )

    project = read_project_version(REPO_ROOT)
    source = _source_metadata()
    distribution = "trusted-beta" if args.trusted_beta else "ordinary"
    target_release_version, package_version = _planned_package_version(
        roots,
        project,
        local_test=args.local_test,
        trusted_beta=args.trusted_beta,
    )
    if args.for_current_install:
        install_plan = _production_install_plan(package_version)
        print("production_install_plan: " + json.dumps(install_plan, separators=(",", ":")))
        _require_install_candidate_allowed(install_plan)
    with _package_build_lock(package_workspace_lock_path(roots)):
        disk = ensure_package_disk_headroom(roots)
        mark_workspace_used(roots)
        print(
            "package_disk_headroom: "
            f"available={disk['availableBytes']} required={disk['requiredBytes']} "
            f"state={disk['statePath']}"
        )
        if not args.local_test:
            _validate_formal_build(
                roots,
                package_version,
                source=source,
                distribution=distribution,
            )
        release_channel = (
            "local-test" if args.local_test else "trusted-beta" if args.trusted_beta else "stable"
        )
        print(f"package_version: {package_version}")
        print(f"release_channel: {release_channel}")
        if target_release_version is not None:
            print(f"target_release_version: {target_release_version}")
        if args.reuse_build_id:
            build_paths = load_build_paths(roots, build_id=args.reuse_build_id)
        else:
            build_paths = create_build_paths(
                roots,
                build_id=new_build_id(str(source["commit"])),
            )
            build_paths.build_dir.mkdir(parents=True)
        roots.cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"build_id: {build_paths.build_id}")
        identity = _prepare_build_contract(
            build_paths,
            product_version=str(package_version),
            source=source,
            local_test=args.local_test,
            trusted_beta=args.trusted_beta,
            profile=profile,
            reuse=bool(args.reuse_build_id),
        )
        print(f"install_profile: {profile.profile_id}")
        if args.step in {"runtime", "all"}:
            _build_runtime(
                build_paths,
                identity=identity,
                offline=args.offline,
                reuse_frozen=bool(args.reuse_build_id),
            )
        if args.step in {"installer", "all"}:
            assert verification_inputs is not None
            setup = _build_installer(
                str(package_version),
                build_paths=build_paths,
                identity=identity,
                profile=profile,
                verification_inputs=verification_inputs,
                local_test=args.local_test,
                trusted_beta=args.trusted_beta,
                offline=args.offline,
            )
            print(f"installer: {setup}")
            if not args.retain_build_workspace:
                try:
                    cleanup_build_after_success(roots, build_paths.build_id)
                    print(f"package_workspace_removed: {build_paths.build_dir}")
                except (OSError, RuntimeError) as error:
                    print(f"warning: package workspace cleanup failed: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
