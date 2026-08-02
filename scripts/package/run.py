"""Build the self-contained Windows installer without reading user credentials."""

from __future__ import annotations

import argparse
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
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from agent.tools.builtin.social_platforms.connectors.xiaohongshu_artifacts import (
    XIAOHONGSHU_COMMIT,
    XIAOHONGSHU_VERSION,
    current_xiaohongshu_artifacts,
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
    discover_formal_releases,
    formal_release_dir,
    latest_formal_version,
    publish_installer,
    sha256,
)
from versioning import ProjectVersion, read_project_version, set_project_version

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
XIAOHONGSHU_LICENSE_SHA256 = "739259a30251c4bb96b60b0f75a701b8ec31dc37eb61167744060c2cd7a88b80"
XIAOHONGSHU_LICENSE_URL = (
    f"https://raw.githubusercontent.com/xpzouying/xiaohongshu-mcp/{XIAOHONGSHU_COMMIT}/LICENSE"
)
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
    """Serialize builds that share incremental PyInstaller and Cargo caches."""

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
                f"another package build is already using this worktree cache: {lock_path}"
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
    dirty = bool(_capture(["git", "status", "--porcelain"]))
    return {"commit": commit, "dirty": dirty}


def _toolchain_versions(pnpm: str) -> dict[str, str]:
    frontend = _read_json(REPO_ROOT / "frontend" / "package.json")
    tauri_cli = str(frontend.get("devDependencies", {}).get("@tauri-apps/cli", "unknown"))
    return {
        "python": platform.python_version(),
        "pnpm": _capture([pnpm, "--version"]),
        "rustc": _capture(["rustc", "--version"]),
        "cargo": _capture(["cargo", "--version"]),
        "tauri_cli": tauri_cli,
    }


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


def _build_runtime(build_paths: BuildPaths, *, offline: bool, reuse_frozen: bool) -> None:
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
                ]
            )

    with _timed_step("runtime-stage-and-smoke", build_paths):
        _stage_runtime(build_paths, dist_dir=dist_dir, offline=offline)


def _stage_runtime(build_paths: BuildPaths, *, dist_dir: Path, offline: bool) -> None:
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
    xhs_artifacts = current_xiaohongshu_artifacts(system="windows", machine="amd64")
    if {artifact.source for artifact in xhs_artifacts} == {"source-build"}:
        builder = _load_xiaohongshu_builder()
        builder.ensure_windows_xiaohongshu_artifacts(
            xhs_dir,
            cache_dir=cache_dir / "xiaohongshu-windows-source-build",
            offline=offline,
        )
        shutil.copy2(builder.LOCK_PATH, xhs_dir / "BUILD-LOCK.json")
        shutil.copy2(builder.PATCH_PATH, xhs_dir / "COMPATIBILITY.patch")
    else:
        for artifact in xhs_artifacts:
            cached = _download(
                url=artifact.url,
                expected_sha256=artifact.sha256,
                destination=cache_dir / f"{XIAOHONGSHU_VERSION}-{artifact.filename}",
                offline=offline,
            )
            shutil.copy2(cached, xhs_dir / artifact.filename)
    xhs_license = _download(
        url=XIAOHONGSHU_LICENSE_URL,
        expected_sha256=XIAOHONGSHU_LICENSE_SHA256,
        destination=cache_dir / f"xiaohongshu-mcp-{XIAOHONGSHU_VERSION}-LICENSE",
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
        f"Version: {XIAOHONGSHU_VERSION}\n"
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
    _smoke_frozen_services(staged_launcher, doctor_env)
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


def _tauri_relative_path(path: Path) -> str:
    """Return a config path relative to src-tauri, as required by frontendDist/resources."""

    try:
        relative = Path(os.path.relpath(path.resolve(), TAURI_DIR.resolve()))
    except ValueError as error:
        raise RuntimeError(
            f"package build directory must be on the same drive as the repository: {path}"
        ) from error
    return relative.as_posix()


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


def _write_package_config(
    build_paths: BuildPaths,
    *,
    local_test: bool,
    source: dict[str, Any],
    trusted_beta: bool,
    credential_store: Any | None = None,
) -> Path:
    config = _read_json(TAURI_CONFIG)
    bundle = config.setdefault("bundle", {})
    resources = bundle.setdefault("resources", {})
    if not isinstance(resources, dict):
        raise RuntimeError("package Tauri resources must be an object")
    resources.pop("package-runtime/runtime/", None)
    resources[build_paths.runtime_stage.as_posix() + "/"] = "runtime"
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
                "buildMode": "local-test" if local_test else "formal",
                "distribution": "trusted-beta" if trusted_beta else "ordinary",
                "sensitive": trusted_beta,
                "buildId": build_paths.build_id,
                "source": source,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    resources[metadata_path.as_posix()] = PACKAGE_METADATA_RESOURCE
    if trusted_beta:
        _cleanup_trusted_resources(build_paths)
        credentials = collect_trusted_credentials(credential_store or KeyringSource())
        artifacts = encrypt_credential_seed(credentials)
        payload, key = _trusted_resource_paths(build_paths)
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(artifacts.payload)
        key.write_bytes(artifacts.key)
        resources[payload.as_posix()] = payload.name
        resources[key.as_posix()] = key.name
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


def _validate_embedded_frontend(executable: Path, entrypoints: list[str]) -> None:
    if not executable.is_file():
        raise RuntimeError(f"built Tauri executable is missing: {executable}")
    binary = executable.read_bytes()
    missing = [name for name in entrypoints if name.encode("utf-8") not in binary]
    if missing:
        raise RuntimeError(
            "built Tauri executable does not contain the complete frontend; "
            "refusing to publish. Missing embedded entrypoints: " + ", ".join(missing)
        )


def _build_installer(
    version: str,
    *,
    build_paths: BuildPaths,
    source: dict[str, Any],
    local_test: bool,
    trusted_beta: bool = False,
    credential_store: Any | None = None,
) -> Path:
    if not (build_paths.runtime_stage / "runtime-inventory.json").is_file():
        raise RuntimeError("runtime stage missing; run the runtime step first")
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is required on the Windows build machine")
    bundle_dir = build_paths.cargo_target_dir / "release" / "bundle" / "nsis"
    candidate: Path | None = None
    try:
        config_path = _write_package_config(
            build_paths,
            local_test=local_test,
            source=source,
            trusted_beta=trusted_beta,
            credential_store=credential_store,
        )
        build_env = _sanitized_env()
        build_env["CARGO_TARGET_DIR"] = str(build_paths.cargo_target_dir)
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
        with _timed_step("tauri-and-nsis", build_paths):
            _run(
                [pnpm, "tauri", "build", "--config", str(config_path), "--bundles", "nsis"],
                cwd=REPO_ROOT / "frontend",
                env=build_env,
            )
        _validate_embedded_frontend(
            build_paths.cargo_target_dir / "release" / "agent-friend.exe",
            entrypoints,
        )
        candidates = sorted(bundle_dir.glob("*.exe"), key=lambda path: path.stat().st_mtime_ns)
        if not candidates:
            raise RuntimeError(f"new NSIS installer not found in {bundle_dir}")
        candidate = candidates[-1]
        distribution = "trusted-beta" if trusted_beta else "ordinary"
        manifest = {
            "schema_version": 2,
            "product": "agent-friend",
            "version": version,
            "platform": "windows-x64",
            "build_mode": "local-test" if local_test else "formal",
            "publishable": not local_test,
            "build_id": build_paths.build_id,
            "created_at": datetime.now(UTC).isoformat(),
            "distribution": distribution,
            "sensitive": trusted_beta,
            "source": source,
            "toolchain": _toolchain_versions(pnpm),
            "installer": {},
            "upgrade_mode": "manual-installer",
            "identifier": "com.agentfriend.desktop",
        }
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
    install_dir: Path, *, expect_trusted_credentials: bool = False
) -> None:
    install_root = install_dir.resolve()
    main_binary = install_root / "agent-friend.exe"
    runtime_dir = install_root / "runtime"
    launcher = runtime_dir / "python" / "agent-friend-runtime.exe"
    inventory = runtime_dir / "runtime-inventory.json"
    model_dir = runtime_dir / MODEL_RELATIVE_DIR
    xhs_dir = runtime_dir / "connectors" / "xiaohongshu"
    xhs_artifacts = current_xiaohongshu_artifacts(system="windows", machine="amd64")
    required = (
        main_binary,
        launcher,
        inventory,
        model_dir,
        xhs_dir / xhs_artifacts[0].filename,
        xhs_dir / xhs_artifacts[1].filename,
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
    _run([str(launcher), "doctor"], cwd=launcher.parent, env=smoke_env)
    if expect_trusted_credentials:
        _run(
            [str(launcher), "credential-status", "--require-operational"],
            cwd=launcher.parent,
            env=smoke_env,
        )
    _smoke_frozen_services(launcher, smoke_env)
    print(f"installed runtime smoke passed: {install_dir.resolve()}")


def _resolve_roots(args: argparse.Namespace) -> PackageRoots:
    return resolve_package_roots(
        REPO_ROOT,
        state_dir=args.state_dir,
        releases_dir=args.releases_dir,
    )


def _release_status(roots: PackageRoots, project: ProjectVersion) -> dict[str, Any]:
    source = _source_metadata()
    records, warnings = discover_formal_releases(roots.releases_root)
    latest = latest_formal_version(records)
    version = str(project.version)
    candidates: dict[str, dict[str, Any]] = {}
    for distribution in ("ordinary", "trusted-beta"):
        destination = formal_release_dir(roots.releases_root, version, distribution)
        candidates[distribution] = {
            "destination": str(destination),
            "exists": destination.exists(),
            "allowed": (
                not source["dirty"]
                and not destination.exists()
                and (latest is None or project.version >= latest)
            ),
        }
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
        "formal": candidates,
        "warnings": warnings,
    }


def _validate_formal_build(
    roots: PackageRoots,
    project: ProjectVersion,
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
    if latest is not None and project.version < latest:
        raise RuntimeError(
            f"project version {project.version} is lower than latest formal release {latest}"
        )
    destination = formal_release_dir(roots.releases_root, str(project.version), distribution)
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
            "installed-smoke",
            "version-status",
            "version-set",
        ),
        nargs="?",
        default="all",
    )
    parser.add_argument("version", nargs="?", help="explicit SemVer used by version-set")
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
        help="build a sensitive installer seeded from the current user's system keyring",
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
    parser.add_argument("--state-dir", type=Path, help="override package cache/work root")
    parser.add_argument("--releases-dir", type=Path, help="override package releases root")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.step == "version-set":
        if args.version is None:
            raise RuntimeError("version-set requires an explicit SemVer")
        if args.local_test or args.trusted_beta or args.reuse_build_id or args.reuse_frozen:
            raise RuntimeError("version-set does not accept package build mode options")
        result = set_project_version(REPO_ROOT, args.version)
        print(
            json.dumps({"project_version": str(result.version), "sources": result.values}, indent=2)
        )
        return 0
    if args.version is not None:
        raise RuntimeError(f"unexpected positional version for {args.step}: {args.version}")
    roots = _resolve_roots(args)
    if args.step == "version-status":
        if args.local_test or args.trusted_beta or args.reuse_build_id or args.reuse_frozen:
            raise RuntimeError("version-status does not accept package build mode options")
        print(json.dumps(_release_status(roots, read_project_version(REPO_ROOT)), indent=2))
        return 0

    _ensure_windows_x64()
    if args.step == "installed-smoke":
        if args.install_dir is None:
            raise RuntimeError("installed-smoke requires --install-dir")
        if args.trusted_beta:
            raise RuntimeError(
                "installed-smoke uses --expect-trusted-credentials, not --trusted-beta"
            )
        _smoke_installed_runtime(
            args.install_dir,
            expect_trusted_credentials=args.expect_trusted_credentials,
        )
        return 0
    if args.expect_trusted_credentials:
        raise RuntimeError("--expect-trusted-credentials requires installed-smoke")
    if args.trusted_beta and args.step == "runtime":
        raise RuntimeError("--trusted-beta requires the installer or all step")
    if args.reuse_frozen:
        raise RuntimeError("--reuse-frozen was replaced by --reuse-build-id")
    if args.step == "installer" and not args.reuse_build_id:
        raise RuntimeError("installer requires --reuse-build-id from a previous runtime build")

    project = read_project_version(REPO_ROOT)
    source = _source_metadata()
    distribution = "trusted-beta" if args.trusted_beta else "ordinary"
    if not args.local_test:
        _validate_formal_build(
            roots,
            project,
            source=source,
            distribution=distribution,
        )
    with _package_build_lock(roots.work_root / "package-build.lock"):
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
        if args.step in {"runtime", "all"}:
            _build_runtime(
                build_paths,
                offline=args.offline,
                reuse_frozen=bool(args.reuse_build_id),
            )
        if args.step in {"installer", "all"}:
            setup = _build_installer(
                str(project.version),
                build_paths=build_paths,
                source=source,
                local_test=args.local_test,
                trusted_beta=args.trusted_beta,
            )
            print(f"installer: {setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
