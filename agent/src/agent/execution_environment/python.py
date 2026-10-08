"""产品管理的 Python execution environment provider。"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from email.parser import Parser
from pathlib import Path
from uuid import uuid4

from .filesystem import filesystem_path, remove_tree
from .manager import environment_root_layout
from .models import (
    EnvironmentStatus,
    ManagedEnvironmentError,
    PreparedProviderEnvironment,
    ProviderHealth,
    ProviderIdentity,
    ResolvedManagedCommand,
)

logger = logging.getLogger(__name__)

EXECUTION_ENVIRONMENT_ROOT_ENV = "AGENT_FRIEND_EXECUTION_ENVIRONMENT_ROOT"
UV_EXECUTABLE_ENV = "AGENT_FRIEND_UV_EXECUTABLE"
PROVIDER_NAME = "python"
IDENTITY_SCHEMA_VERSION = 2
SESSION_MANIFEST_SCHEMA_VERSION = 1
OVERLAY_MAX_BYTES = 1024 * 1024 * 1024
OVERLAY_MAX_FILES = 25_000
SHARED_CACHE_HIGH_BYTES = 4 * 1024 * 1024 * 1024
SHARED_CACHE_TARGET_BYTES = 3 * 1024 * 1024 * 1024
INACTIVE_OVERLAYS_HIGH_BYTES = 8 * 1024 * 1024 * 1024
STAGING_MAX_AGE_SECONDS = 24 * 60 * 60
LOCK_STALE_SECONDS = 15 * 60
LOCK_WAIT_SECONDS = 30.0
MIN_PACKAGE_FREE_BYTES = 256 * 1024 * 1024
_PYTHON_ALIASES = {"python", "python.exe", "python3", "python3.exe"}
_PIP_ALIASES = {"pip", "pip.exe", "pip3", "pip3.exe"}
_MUTATING_PACKAGE_ACTIONS = {"install", "uninstall"}
_PACKAGE_ACTIONS = _MUTATING_PACKAGE_ACTIONS | {"list", "show", "freeze", "check", "tree"}
_REJECTED_PACKAGE_OPTIONS = {
    "--cache-dir",
    "--config-file",
    "--constraint",
    "--editable",
    "--extra-index-url",
    "--find-links",
    "--index-url",
    "--keyring-provider",
    "--no-binary",
    "--no-python-downloads",
    "--prefix",
    "--python",
    "--python-preference",
    "--root",
    "--requirement",
    "--system",
    "--target",
    "--trusted-host",
    "-c",
    "-r",
}


class PythonEnvironmentProvider:
    """固定 base + 每 session 标准 venv + 共享 uv wheel cache。"""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        data_root: Path,
        base_executable: Path,
        uv_executable: Path | None,
        identity: ProviderIdentity,
        base_root: Path | None = None,
        packaged_manifest: Mapping[str, object] | None = None,
        overlay_max_bytes: int = OVERLAY_MAX_BYTES,
        overlay_max_files: int = OVERLAY_MAX_FILES,
        shared_cache_high_bytes: int = SHARED_CACHE_HIGH_BYTES,
        shared_cache_target_bytes: int = SHARED_CACHE_TARGET_BYTES,
        inactive_overlays_high_bytes: int = INACTIVE_OVERLAYS_HIGH_BYTES,
    ) -> None:
        self._data_root = data_root.resolve()
        self._layout = environment_root_layout(self._data_root)
        self._base_executable = base_executable.resolve()
        self._base_root = (base_root or self._base_executable.parent).resolve()
        self._uv_executable = uv_executable.resolve() if uv_executable is not None else None
        self.identity = identity
        self._packaged_manifest = dict(packaged_manifest or {})
        self._overlay_max_bytes = overlay_max_bytes
        self._overlay_max_files = overlay_max_files
        self._shared_cache_high_bytes = shared_cache_high_bytes
        self._shared_cache_target_bytes = shared_cache_target_bytes
        self._inactive_overlays_high_bytes = inactive_overlays_high_bytes
        self._global_health: ProviderHealth | None = None
        self._session_cache: dict[str, PreparedProviderEnvironment] = {}
        self._session_cache_lock = threading.RLock()
        self._last_gc_status = "not-run"
        self._shared_cache_root = self._layout["shared_cache"] / f"uv-{self.identity.identity[:24]}"

    @classmethod
    def from_runtime(
        cls,
        *,
        data_root: Path,
        environment_root: Path | None = None,
        uv_executable: Path | None = None,
    ) -> PythonEnvironmentProvider:
        """按显式 packaged root 或当前 source runtime 构造 provider。"""
        configured_root = environment_root
        if configured_root is None:
            raw_root = os.environ.get(EXECUTION_ENVIRONMENT_ROOT_ENV)
            configured_root = Path(raw_root) if raw_root else None
        if configured_root is not None:
            return cls._from_packaged_root(data_root=data_root, root=configured_root)

        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if uv_executable is None:
            raw_uv = os.environ.get(UV_EXECUTABLE_ENV)
            resolved_uv = raw_uv or shutil.which("uv")
            uv_executable = Path(resolved_uv) if resolved_uv else None
        version = platform.python_version()
        digest = _identity_digest(
            "source",
            str(base_executable),
            _sha256_file(base_executable) if base_executable.is_file() else "missing",
            str(uv_executable.resolve()) if uv_executable is not None else "missing",
        )
        identity = ProviderIdentity(
            provider=PROVIDER_NAME,
            runtime_version=version,
            identity=digest,
            platform=sys.platform,
            architecture=platform.machine().casefold(),
        )
        return cls(
            data_root=data_root,
            base_executable=base_executable,
            uv_executable=uv_executable,
            identity=identity,
        )

    @classmethod
    def _from_packaged_root(cls, *, data_root: Path, root: Path) -> PythonEnvironmentProvider:
        provider_root = root.resolve() / PROVIDER_NAME
        manifest_path = provider_root / "identity.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ManagedEnvironmentError(
                "managed_environment_unavailable",
                "受管 Python identity manifest 缺失或无效。",
            ) from exc
        if not isinstance(manifest, dict):
            raise ManagedEnvironmentError(
                "managed_environment_unavailable",
                "受管 Python identity manifest 结构无效。",
            )
        runtime_version = _required_manifest_string(manifest, "runtimeVersion")
        identity_value = _required_manifest_string(manifest, "identity")
        manifest_platform = _required_manifest_string(manifest, "platform")
        architecture = _required_manifest_string(manifest, "architecture")
        base_relative = _required_manifest_string(manifest, "baseExecutable")
        uv_relative = _required_manifest_string(manifest, "uvExecutable")
        return cls(
            data_root=data_root,
            base_executable=_safe_manifest_path(provider_root, base_relative),
            uv_executable=_safe_manifest_path(provider_root, uv_relative),
            base_root=provider_root / "base",
            identity=ProviderIdentity(
                provider=PROVIDER_NAME,
                runtime_version=runtime_version,
                identity=identity_value,
                platform=manifest_platform,
                architecture=architecture,
            ),
            packaged_manifest=manifest,
        )

    def prepare_global(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
            self._validate_packaged_manifest()
            python_probe = self._run_python_probe(self._base_executable, expect_venv=False)
            actual_version = str(python_probe["version"])
            if actual_version != self.identity.runtime_version:
                raise ManagedEnvironmentError(
                    "managed_environment_unavailable",
                    "受管 Python 版本与产品 identity 不一致。",
                )
            status = EnvironmentStatus.READY
            error_code = None
            message = ""
            if self._uv_executable is None or not self._uv_executable.is_file():
                if self._packaged_manifest:
                    raise ManagedEnvironmentError(
                        "managed_environment_unavailable",
                        "产品受管 uv payload 缺失。",
                    )
                status = EnvironmentStatus.DEGRADED
                error_code = "managed_package_runtime_unavailable"
                message = "source runtime 未找到受管 uv；Python 脚本仍可运行。"
            else:
                self._run_uv_probe()
            for path in self._layout.values():
                path.mkdir(parents=True, exist_ok=True)
            self._shared_cache_root.mkdir(parents=True, exist_ok=True)
            health = ProviderHealth(
                identity=self.identity,
                status=status,
                error_code=error_code,
                message=message,
                prepare_duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            health = ProviderHealth(
                identity=self.identity,
                status=EnvironmentStatus.UNAVAILABLE,
                error_code=getattr(exc, "code", "managed_environment_unavailable"),
                message=str(exc),
                prepare_duration_seconds=time.monotonic() - started,
            )
        self._global_health = health
        if health.status is not EnvironmentStatus.UNAVAILABLE:
            try:
                self.gc(active_session_keys=frozenset())
            except OSError:
                health = dataclasses.replace(
                    health,
                    status=EnvironmentStatus.DEGRADED,
                    error_code="managed_environment_gc_failed",
                    message="受管执行环境启动清理失败；现有解释器仍可使用。",
                )
                self._global_health = health
        return health

    def prepare_session(
        self,
        session_key: str,
        health: ProviderHealth,
    ) -> PreparedProviderEnvironment:
        started = time.monotonic()
        if health.status is EnvironmentStatus.UNAVAILABLE:
            return self._unavailable_environment(session_key, health)
        with self._session_cache_lock:
            cached = self._session_cache.get(session_key)
        if cached is not None:
            return dataclasses.replace(
                cached,
                prepare_duration_seconds=time.monotonic() - started,
            )
        session_root = self._session_environment_root(session_key)
        lock_path = self._layout["locks"] / f"{session_key}-{self.identity.identity[:16]}.lock"
        with _exclusive_file_lock(lock_path):
            valid = self._load_valid_session_environment(session_key, session_root)
            if valid is not None:
                return valid
            staging = self._layout["staging"] / (
                f"{session_key}-{self.identity.identity[:16]}-{uuid4().hex}"
            )
            try:
                staging.mkdir(parents=True, exist_ok=False)
                venv_root = staging / "venv"
                subprocess.run(
                    [
                        str(self._base_executable),
                        "-I",
                        "-B",
                        "-m",
                        "venv",
                        "--without-pip",
                        str(venv_root),
                    ],
                    cwd=self._data_root,
                    env=self._base_environment(),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                venv_python = _venv_python(venv_root)
                self._run_python_probe(venv_python, expect_venv=True)
                self._install_baseline(venv_python)
                rebuild_lock = session_root.parent / "rebuild-packages.lock"
                rebuild_status, rebuild_error, restored_packages, revision = (
                    self._restore_gc_packages(rebuild_lock, venv_python)
                )
                file_count, byte_count = _tree_usage(staging)
                if file_count > self._overlay_max_files or byte_count > self._overlay_max_bytes:
                    raise ManagedEnvironmentError(
                        "managed_environment_quota_exceeded",
                        "新建 Python session environment 超出产品资源上限。",
                    )
                manifest = {
                    "schemaVersion": SESSION_MANIFEST_SCHEMA_VERSION,
                    "provider": PROVIDER_NAME,
                    "providerIdentity": self.identity.identity,
                    "runtimeVersion": self.identity.runtime_version,
                    "revision": revision,
                    "createdAtEpochSeconds": int(time.time()),
                    "status": rebuild_status.value,
                    "errorCode": rebuild_error,
                }
                _atomic_json_write(staging / "manifest.json", manifest)
                _atomic_json_write(
                    staging / "packages.lock",
                    {
                        "schemaVersion": 1,
                        "providerIdentity": self.identity.identity,
                        "revision": revision,
                        "packages": restored_packages,
                    },
                )
                session_root.parent.mkdir(parents=True, exist_ok=True)
                prepared = self._publish_session_environment(
                    session_key, staging, session_root, revision
                )
                if rebuild_status is EnvironmentStatus.READY:
                    rebuild_lock.unlink(missing_ok=True)
            finally:
                if filesystem_path(staging).exists():
                    self._discard_environment(staging)
        result = dataclasses.replace(
            prepared,
            prepare_duration_seconds=time.monotonic() - started,
        )
        with self._session_cache_lock:
            self._session_cache[session_key] = result
        return result

    def _publish_session_environment(
        self, session_key: str, staging: Path, session_root: Path, revision: int
    ) -> PreparedProviderEnvironment:
        # Keep the old tree intact until the replacement works at its final path.
        # Recovery trees are excluded from staging GC if rollback itself fails.
        backup: Path | None = None
        if filesystem_path(session_root).exists():
            backup = self._layout["staging"] / f".recovery-{uuid4().hex}"
            os.replace(filesystem_path(session_root), filesystem_path(backup))
        published = False
        try:
            os.replace(filesystem_path(staging), filesystem_path(session_root))
            published = True
            prepared = self._load_valid_session_environment(session_key, session_root)
            if prepared is None:
                raise ManagedEnvironmentError(
                    "managed_environment_prepare_failed",
                    "Python session environment 发布后校验失败，已尝试恢复旧环境。",
                )
            _atomic_json_write(
                session_root.parent / "current.json",
                {
                    "schemaVersion": 1,
                    "provider": PROVIDER_NAME,
                    "providerIdentity": self.identity.identity,
                    "revision": revision,
                },
            )
        except Exception:
            try:
                if published:
                    os.replace(filesystem_path(session_root), filesystem_path(staging))
                if backup is not None:
                    os.replace(filesystem_path(backup), filesystem_path(session_root))
            except OSError:
                logger.exception(
                    "session execution environment rollback failed session=%s backup=%s",
                    session_key,
                    backup.name if backup is not None else "none",
                )
            raise
        if backup is not None:
            # Only committed replacements become disposable. A crash or failed
            # rollback must not let the normal 24-hour staging GC erase evidence.
            retired = self._layout["staging"] / f"retired-{uuid4().hex}"
            try:
                os.replace(filesystem_path(backup), filesystem_path(retired))
                os.utime(filesystem_path(retired), None)
            except OSError:
                logger.exception(
                    "session execution environment backup retained session=%s backup=%s",
                    session_key,
                    backup.name,
                )
            else:
                self._discard_environment(retired)
        return prepared

    @staticmethod
    def _discard_environment(path: Path) -> None:
        try:
            remove_tree(path)
        except OSError:
            logger.warning(
                "execution environment cleanup deferred entry=%s", path.name, exc_info=True
            )

    def resolve_command(
        self,
        environment: PreparedProviderEnvironment,
        argv: tuple[str, ...],
    ) -> ResolvedManagedCommand | None:
        if not argv:
            return None
        if Path(argv[0]).expanduser().is_absolute() or any(
            separator in argv[0] for separator in ("/", "\\")
        ):
            return None
        executable_name = Path(argv[0]).name.casefold()
        package_args = _package_arguments(argv)
        managed_alias = executable_name in _PYTHON_ALIASES or executable_name in _PIP_ALIASES
        managed_alias = managed_alias or package_args is not None
        if not managed_alias:
            return None
        if environment.status is EnvironmentStatus.UNAVAILABLE:
            raise ManagedEnvironmentError(
                environment.error_code or "managed_environment_unavailable",
                environment.message or "受管 Python 当前不可用。",
            )
        if package_args is not None:
            return self._resolve_package_command(environment, argv, package_args)
        executable = environment.executable_aliases.get(executable_name)
        if executable is None:
            return None
        return ResolvedManagedCommand(
            provider=environment,
            argv=(str(executable), *argv[1:]),
            display_argv=argv,
            environment=dict(environment.environment),
            read_only_roots=(*environment.read_only_roots, *environment.writable_roots),
            writable_roots=(),
        )

    def matches_command(self, argv: tuple[str, ...]) -> bool:
        if (
            not argv
            or Path(argv[0]).expanduser().is_absolute()
            or any(separator in argv[0] for separator in ("/", "\\"))
        ):
            return False
        executable_name = Path(argv[0]).name.casefold()
        return (
            executable_name in _PYTHON_ALIASES
            or executable_name in _PIP_ALIASES
            or _package_arguments(argv) is not None
        )

    def record_command_success(
        self,
        command: ResolvedManagedCommand,
    ) -> PreparedProviderEnvironment:
        if not command.package_operation or not command.requires_write:
            return command.provider
        session_key = command.provider.session_key
        root = self._session_environment_root(session_key)
        lock_path = self._layout["locks"] / f"{session_key}-{self.identity.identity[:16]}.lock"
        with _exclusive_file_lock(lock_path):
            manifest_path = root / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ManagedEnvironmentError(
                    "managed_environment_commit_failed",
                    "package operation 已执行，但 session manifest 无法安全更新。",
                ) from exc
            file_count, byte_count = _tree_usage(root)
            if file_count > self._overlay_max_files or byte_count > self._overlay_max_bytes:
                manifest["invalidReason"] = "managed_environment_quota_exceeded"
                _atomic_json_write(manifest_path, manifest)
                with self._session_cache_lock:
                    self._session_cache.pop(session_key, None)
                raise ManagedEnvironmentError(
                    "managed_environment_quota_exceeded",
                    "package operation 后 Python session environment 超出产品资源上限。",
                )
            revision = int(manifest.get("revision", 0)) + 1
            packages = _installed_distributions(root / "venv")
            _atomic_json_write(
                root / "packages.lock",
                {
                    "schemaVersion": 1,
                    "providerIdentity": self.identity.identity,
                    "revision": revision,
                    "packages": packages,
                },
            )
            manifest["revision"] = revision
            manifest["updatedAtEpochSeconds"] = int(time.time())
            manifest["status"] = (
                self._global_health.status.value
                if self._global_health is not None
                else EnvironmentStatus.READY.value
            )
            manifest["errorCode"] = self._global_health.error_code if self._global_health else None
            manifest.pop("invalidReason", None)
            _atomic_json_write(manifest_path, manifest)
            (root.parent / "rebuild-packages.lock").unlink(missing_ok=True)
        updated = dataclasses.replace(command.provider, revision=revision)
        with self._session_cache_lock:
            self._session_cache[session_key] = updated
        return updated

    def release_session(self, session_key: str) -> None:
        with self._session_cache_lock:
            self._session_cache.pop(session_key, None)
        root = self._layout["sessions"] / f"session-{session_key}"
        if root.exists():
            remove_tree(root)

    def collect_health(self) -> ProviderHealth:
        return self._global_health or ProviderHealth(
            identity=self.identity,
            status=EnvironmentStatus.UNAVAILABLE,
            error_code="managed_environment_not_started",
        )

    def collect_diagnostics(
        self,
        *,
        active_session_keys: frozenset[str],
    ) -> dict[str, object]:
        health = self.collect_health()
        session_root = self._layout["sessions"]
        active_count = 0
        inactive_count = 0
        total_bytes = 0
        if session_root.is_dir():
            for candidate in session_root.glob("session-*"):
                key = candidate.name.removeprefix("session-")
                if key in active_session_keys:
                    active_count += 1
                else:
                    inactive_count += 1
                total_bytes += _tree_usage(candidate)[1]
        return {
            "provider": self.identity.provider,
            "status": health.status.value,
            "runtimeVersion": self.identity.runtime_version,
            "identity": self.identity.identity[:16],
            "platform": self.identity.platform,
            "architecture": self.identity.architecture,
            "errorCode": health.error_code,
            "prepareDurationBucket": _duration_bucket(health.prepare_duration_seconds),
            "sessionOverlays": {
                "activeCount": active_count,
                "inactiveCount": inactive_count,
                "totalBytesBucket": _byte_bucket(total_bytes),
            },
            "sharedCache": {
                "bytesBucket": _byte_bucket(_tree_usage(self._shared_cache_root)[1]),
            },
            "lastGc": {"status": self._last_gc_status},
            "runtimePayload": {
                "declared": bool(self._packaged_manifest),
                "validated": bool(self._packaged_manifest)
                and health.status is not EnvironmentStatus.UNAVAILABLE,
            },
        }

    def gc(self, *, active_session_keys: frozenset[str]) -> dict[str, int]:
        try:
            staging_removed = self._cleanup_staging()
            cache_bytes = _tree_usage(self._shared_cache_root)[1]
            cache_pruned = 0
            if (
                not active_session_keys
                and cache_bytes > self._shared_cache_high_bytes
                and self._shared_cache_target_bytes < cache_bytes
            ):
                remove_tree(self._shared_cache_root)
                self._shared_cache_root.mkdir(parents=True, exist_ok=True)
                cache_pruned = cache_bytes
                cache_bytes = 0

            sessions_root = self._layout["sessions"]
            candidates: list[tuple[float, str, Path, int]] = []
            inactive_bytes = 0
            if sessions_root.is_dir():
                for candidate in sessions_root.glob("session-*"):
                    key = candidate.name.removeprefix("session-")
                    if key in active_session_keys:
                        continue
                    size = _tree_usage(candidate)[1]
                    inactive_bytes += size
                    candidates.append((candidate.stat().st_mtime, key, candidate, size))
            overlays_removed = 0
            for _modified, key, candidate, size in sorted(candidates):
                if inactive_bytes <= self._inactive_overlays_high_bytes:
                    break
                desired_lock = _latest_packages_lock(candidate)
                lock_bytes = (
                    desired_lock.read_bytes()
                    if desired_lock is not None and desired_lock.stat().st_size <= 1024 * 1024
                    else None
                )
                remove_tree(candidate)
                candidate.mkdir(parents=True)
                if lock_bytes is not None:
                    (candidate / "rebuild-packages.lock").write_bytes(lock_bytes)
                with self._session_cache_lock:
                    self._session_cache.pop(key, None)
                inactive_bytes -= size
                overlays_removed += 1
            self._last_gc_status = "complete"
            return {
                "staging_removed": staging_removed,
                "inactive_sessions": len(candidates) - overlays_removed,
                "inactive_bytes": inactive_bytes,
                "overlays_removed": overlays_removed,
                "shared_cache_bytes": cache_bytes,
                "shared_cache_pruned_bytes": cache_pruned,
            }
        except OSError:
            self._last_gc_status = "failed"
            raise

    def _resolve_package_command(
        self,
        environment: PreparedProviderEnvironment,
        display_argv: tuple[str, ...],
        package_args: tuple[str, ...],
    ) -> ResolvedManagedCommand:
        if self._uv_executable is None or not self._uv_executable.is_file():
            raise ManagedEnvironmentError(
                "managed_environment_unavailable",
                "受管 package runtime 不可用。",
            )
        if not package_args or package_args[0].casefold() not in _PACKAGE_ACTIONS:
            raise ManagedEnvironmentError(
                "managed_package_source_unsupported",
                "首期仅支持 install、uninstall、list、show、freeze、check 和 tree。",
            )
        action = package_args[0].casefold()
        user_args = _validate_package_arguments(package_args)
        if action in _MUTATING_PACKAGE_ACTIONS:
            session_root = self._session_environment_root(environment.session_key)
            file_count, byte_count = _tree_usage(session_root)
            try:
                free_bytes = shutil.disk_usage(session_root).free
            except OSError as exc:
                raise ManagedEnvironmentError(
                    "managed_environment_quota_exceeded",
                    "无法确认受管环境的可用磁盘空间；package operation 已拒绝。",
                ) from exc
            if (
                file_count >= self._overlay_max_files
                or byte_count >= self._overlay_max_bytes
                or free_bytes < MIN_PACKAGE_FREE_BYTES
            ):
                raise ManagedEnvironmentError(
                    "managed_environment_quota_exceeded",
                    "受管 Python session environment 已达到资源或磁盘余量边界。",
                )
        venv_python = environment.executable_aliases["python"]
        command = [
            str(self._uv_executable),
            "--no-config",
            "--no-python-downloads",
            "--cache-dir",
            str(self._shared_cache_root),
            "pip",
            action,
            "--python",
            str(venv_python),
        ]
        if action == "install":
            command.extend(("--no-build", "--link-mode", "copy"))
        command.extend(user_args[1:])
        local_wheels_only = action == "install" and _only_local_wheels(user_args[1:])
        cache_hit = (
            self._cache_contains_requirements(user_args[1:])
            if action == "install" and not local_wheels_only
            else False
            if action == "install"
            else None
        )
        mutating = action in _MUTATING_PACKAGE_ACTIONS
        return ResolvedManagedCommand(
            provider=environment,
            argv=tuple(command),
            display_argv=display_argv,
            environment=dict(environment.environment),
            read_only_roots=(
                environment.read_only_roots
                if mutating
                else (
                    *environment.read_only_roots,
                    *environment.writable_roots,
                    self._shared_cache_root,
                )
            ),
            writable_roots=(
                (*environment.writable_roots, self._shared_cache_root) if mutating else ()
            ),
            package_operation=True,
            requires_network=action == "install" and not local_wheels_only,
            requires_write=mutating,
            cache_hit=cache_hit,
        )

    def classify_failure(
        self,
        command: ResolvedManagedCommand,
        output: str,
    ) -> tuple[str, str] | None:
        if not command.package_operation:
            return None
        normalized = output.casefold()
        if any(
            marker in normalized
            for marker in (
                "no solution found",
                "no wheels are available",
                "was not found in the package registry",
                "requires-python",
                "cannot be installed because there are no compatible wheels",
            )
        ):
            return (
                "managed_package_distribution_unavailable",
                "当前 Python/平台没有满足约束的兼容 wheel；未回退到源码构建。",
            )
        if any(
            marker in normalized
            for marker in (
                "hash mismatch",
                "failed to read from the cache",
                "cache entry",
            )
        ):
            return (
                "managed_cache_integrity_failed",
                "受管依赖 cache 无法安全复用，安装结果未发布。",
            )
        if any(
            marker in normalized
            for marker in (
                "failed to download",
                "error sending request",
                "network connectivity",
                "connection refused",
                "timed out",
            )
        ):
            return (
                "managed_package_source_unavailable",
                "受管 package source 当前不可达，安装结果未发布。",
            )
        return (
            "managed_package_failed",
            "受管 package operation 失败，环境 revision 未改变。",
        )

    def _load_valid_session_environment(
        self,
        session_key: str,
        root: Path,
    ) -> PreparedProviderEnvironment | None:
        if not filesystem_path(root).exists():
            return None
        phase = "manifest_read"
        try:
            manifest = json.loads(
                filesystem_path(root / "manifest.json").read_text(encoding="utf-8")
            )
            reason = None
            if not isinstance(manifest, dict):
                reason = "manifest_not_object"
            elif manifest.get("schemaVersion") != SESSION_MANIFEST_SCHEMA_VERSION:
                reason = "manifest_schema_mismatch"
            elif manifest.get("providerIdentity") != self.identity.identity:
                reason = "provider_identity_mismatch"
            elif not isinstance(manifest.get("revision"), int):
                reason = "manifest_revision_invalid"
            elif manifest.get("invalidReason") is not None:
                reason = "manifest_marked_invalid"
            if reason is not None:
                logger.warning(
                    "session execution environment invalid session=%s reason=%s",
                    session_key,
                    reason,
                )
                return None
            venv_root = root / "venv"
            venv_python = _venv_python(venv_root)
            phase = "python_probe"
            self._run_python_probe(venv_python, expect_venv=True)
        except (OSError, ValueError, subprocess.SubprocessError, ManagedEnvironmentError) as exc:
            logger.warning(
                "session execution environment invalid session=%s phase=%s "
                "error_type=%s errno=%s winerror=%s returncode=%s code=%s",
                session_key,
                phase,
                type(exc).__name__,
                getattr(exc, "errno", None),
                getattr(exc, "winerror", None),
                getattr(exc, "returncode", None),
                getattr(exc, "code", None),
            )
            return None
        aliases = {name: venv_python for name in _PYTHON_ALIASES}
        return PreparedProviderEnvironment(
            identity=self.identity,
            status=(
                EnvironmentStatus(str(manifest.get("status")))
                if manifest.get("status") in {status.value for status in EnvironmentStatus}
                else self._global_health.status
                if self._global_health is not None
                else EnvironmentStatus.READY
            ),
            session_key=session_key,
            revision=int(manifest["revision"]),
            executable_aliases=aliases,
            environment=self._session_environment(venv_root),
            read_only_roots=tuple(
                root
                for root in (
                    self._base_root,
                    self._uv_executable.parent if self._uv_executable else None,
                )
                if root is not None
            ),
            writable_roots=(root,),
            shared_cache_root=self._shared_cache_root,
            error_code=(
                str(manifest["errorCode"])
                if manifest.get("errorCode") not in (None, "")
                else self._global_health.error_code
                if self._global_health
                else None
            ),
            message=(self._global_health.message if self._global_health else ""),
        )

    def _session_environment_root(self, session_key: str) -> Path:
        return (
            self._layout["sessions"]
            / f"session-{session_key}"
            / f"python-{self.identity.identity[:24]}"
        )

    def _unavailable_environment(
        self,
        session_key: str,
        health: ProviderHealth,
    ) -> PreparedProviderEnvironment:
        return PreparedProviderEnvironment(
            identity=self.identity,
            status=EnvironmentStatus.UNAVAILABLE,
            session_key=session_key,
            error_code=health.error_code or "managed_environment_unavailable",
            message=health.message,
            prepare_duration_seconds=health.prepare_duration_seconds,
        )

    def _validate_packaged_manifest(self) -> None:
        if not self._packaged_manifest:
            return
        if self._packaged_manifest.get("schemaVersion") != IDENTITY_SCHEMA_VERSION:
            raise ManagedEnvironmentError(
                "managed_environment_unavailable",
                "受管 Python identity schema 不受支持。",
            )
        if self._packaged_manifest.get("provider") != PROVIDER_NAME:
            raise ManagedEnvironmentError(
                "managed_environment_unavailable",
                "受管 Python provider identity 不匹配。",
            )
        files = self._packaged_manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ManagedEnvironmentError(
                "managed_environment_unavailable",
                "受管 Python identity 未声明文件清单。",
            )
        provider_root = self._base_root.parent
        for entry in files:
            if not isinstance(entry, dict):
                raise ManagedEnvironmentError(
                    "managed_environment_unavailable", "受管 Python 文件清单无效。"
                )
            relative = _required_manifest_string(entry, "path")
            expected = _required_manifest_string(entry, "sha256")
            target = _safe_manifest_path(provider_root, relative)
            if not target.is_file() or _sha256_file(target) != expected:
                raise ManagedEnvironmentError(
                    "managed_environment_unavailable",
                    "受管 Python payload 缺失或完整性校验失败。",
                )
        current_platform = "windows" if os.name == "nt" else sys.platform
        if self.identity.platform.casefold() != current_platform.casefold():
            raise ManagedEnvironmentError(
                "managed_environment_unavailable", "受管 Python 平台 identity 不匹配。"
            )

    def _run_python_probe(self, executable: Path, *, expect_venv: bool) -> dict[str, object]:
        script = (
            "import csv,json,sqlite3,ssl,sys,tarfile,zipfile;"
            "print(json.dumps({'version':'.'.join(map(str,sys.version_info[:3])),"
            "'venv':sys.prefix!=sys.base_prefix},sort_keys=True))"
        )
        completed = subprocess.run(
            [str(executable), "-I", "-B", "-c", script],
            cwd=self._data_root,
            env=self._base_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=10,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, dict) or payload.get("venv") is not expect_venv:
            raise ManagedEnvironmentError(
                "managed_environment_unavailable", "受管 Python 隔离健康检查失败。"
            )
        return payload

    def _run_uv_probe(self) -> None:
        assert self._uv_executable is not None
        completed = subprocess.run(
            [str(self._uv_executable), "--version"],
            cwd=self._data_root,
            env=self._base_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=10,
            text=True,
            encoding="utf-8",
        )
        if not completed.stdout.strip().startswith("uv "):
            raise ManagedEnvironmentError(
                "managed_environment_unavailable", "受管 uv identity 健康检查失败。"
            )

    def _install_baseline(self, executable: Path) -> None:
        requirements = self._packaged_manifest.get("baselineRequirements", [])
        if not requirements:
            return
        if not isinstance(requirements, list) or not all(
            isinstance(item, str) and "==" in item and not item.startswith("-")
            for item in requirements
        ):
            raise ManagedEnvironmentError(
                "managed_environment_unavailable", "产品 Python 依赖基线无效。"
            )
        if self._uv_executable is None:
            raise ManagedEnvironmentError(
                "managed_environment_unavailable", "产品 Python 依赖准备缺少 uv。"
            )
        subprocess.run(
            [
                str(self._uv_executable),
                "--no-config",
                "--no-python-downloads",
                "--cache-dir",
                str(self._shared_cache_root),
                "pip",
                "install",
                "--python",
                str(executable),
                "--no-index",
                "--no-build",
                "--link-mode",
                "copy",
                "--find-links",
                str(self._base_root.parent / "baseline-wheels"),
                *requirements,
            ],
            cwd=executable.parent,
            env={
                **self._base_environment(),
                "TEMP": str(executable.parent),
                "TMP": str(executable.parent),
                "TMPDIR": str(executable.parent),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=60,
        )

    def _base_environment(self) -> dict[str, str]:
        allowed = ("COMSPEC", "SYSTEMDRIVE", "SYSTEMROOT", "WINDIR")
        environment = {key: os.environ[key] for key in allowed if key in os.environ}
        environment.update(
            {
                "PATH": os.pathsep.join(
                    str(path)
                    for path in (
                        self._base_executable.parent,
                        self._uv_executable.parent if self._uv_executable else None,
                    )
                    if path is not None
                ),
                "PYTHONNOUSERSITE": "1",
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
                "UV_NO_CONFIG": "1",
                "UV_NO_PYTHON_DOWNLOADS": "1",
                "UV_KEYRING_PROVIDER": "disabled",
            }
        )
        return environment

    def _session_environment(self, venv_root: Path) -> dict[str, str]:
        environment = self._base_environment()
        environment["VIRTUAL_ENV"] = str(venv_root)
        environment["PATH"] = os.pathsep.join(
            (str(_venv_python(venv_root).parent), environment["PATH"])
        )
        return environment

    def _cleanup_staging(self) -> int:
        staging_root = self._layout["staging"]
        if not staging_root.is_dir():
            return 0
        cutoff = time.time() - STAGING_MAX_AGE_SECONDS
        removed = 0
        for candidate in staging_root.iterdir():
            if candidate.name.startswith(".recovery-"):
                continue
            try:
                native = filesystem_path(candidate)
                if native.stat().st_mtime >= cutoff:
                    continue
                if native.is_dir():
                    remove_tree(candidate)
                else:
                    native.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _restore_gc_packages(
        self,
        rebuild_lock: Path,
        venv_python: Path,
    ) -> tuple[EnvironmentStatus, str | None, list[dict[str, str]], int]:
        default_status = (
            self._global_health.status
            if self._global_health is not None
            else EnvironmentStatus.READY
        )
        default_error = self._global_health.error_code if self._global_health else None
        if not rebuild_lock.is_file():
            return default_status, default_error, [], 1
        try:
            value = json.loads(rebuild_lock.read_text(encoding="utf-8"))
            packages = value.get("packages")
            previous_revision = int(value.get("revision", 0))
        except (OSError, ValueError, TypeError):
            return EnvironmentStatus.DEGRADED, "managed_environment_rebuild_lock_invalid", [], 1
        if not isinstance(packages, list) or not all(isinstance(item, dict) for item in packages):
            return EnvironmentStatus.DEGRADED, "managed_environment_rebuild_lock_invalid", [], 1
        normalized = [
            {
                "name": str(item.get("name", "")),
                "version": str(item.get("version", "")),
                "source": str(item.get("source", "")),
            }
            for item in packages
            if item.get("name") and item.get("version")
        ]
        desired = [
            item
            for item in normalized
            if item["name"].casefold() not in {"pip", "setuptools", "wheel"}
        ]
        revision = max(1, previous_revision + 1)
        if not desired:
            return default_status, default_error, normalized, revision
        if any(item["source"] == "local-wheel" for item in desired):
            return (
                EnvironmentStatus.DEGRADED,
                "managed_environment_rebuild_requires_local_wheel",
                [],
                revision,
            )
        if self._uv_executable is None or not self._uv_executable.is_file():
            return EnvironmentStatus.DEGRADED, "managed_package_runtime_unavailable", [], revision
        specs = [f"{item['name']}=={item['version']}" for item in desired]
        try:
            subprocess.run(
                [
                    str(self._uv_executable),
                    "--offline",
                    "--no-config",
                    "--no-python-downloads",
                    "--cache-dir",
                    str(self._shared_cache_root),
                    "pip",
                    "install",
                    "--python",
                    str(venv_python),
                    "--no-build",
                    "--link-mode",
                    "copy",
                    *specs,
                ],
                cwd=self._data_root,
                env=self._base_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return EnvironmentStatus.DEGRADED, "managed_environment_cache_miss", [], revision
        return default_status, default_error, normalized, revision

    def _cache_contains_requirements(self, arguments: Sequence[str]) -> bool:
        requirements = [
            value
            for value in arguments
            if not value.startswith("-") and not value.casefold().endswith(".whl")
        ]
        if not requirements or not self._shared_cache_root.is_dir():
            return False
        cache_parts = {
            part.casefold().replace("_", "-")
            for path in self._shared_cache_root.rglob("*")
            for part in path.parts
        }
        for requirement in requirements:
            name, separator, version = requirement.partition("==")
            normalized_name = name.strip().casefold().replace("_", "-")
            if not separator or not version or normalized_name not in cache_parts:
                return False
            if version.strip().casefold() not in cache_parts:
                return False
        return True


def _package_arguments(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    executable = Path(argv[0]).name.casefold()
    if executable in _PIP_ALIASES:
        return argv[1:]
    if executable in _PYTHON_ALIASES and len(argv) >= 3 and argv[1:3] == ("-m", "pip"):
        return argv[3:]
    if executable in {"uv", "uv.exe"} and len(argv) >= 2 and argv[1].casefold() == "pip":
        return argv[2:]
    return None


def _validate_package_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    for index, value in enumerate(arguments):
        option = value.split("=", 1)[0].casefold()
        if option in _REJECTED_PACKAGE_OPTIONS or value == "-e":
            raise ManagedEnvironmentError(
                "managed_package_source_unsupported",
                f"首期受管 package operation 不支持参数 {value!r}。",
            )
        if index == 0 or value.startswith("-"):
            continue
        lowered = value.casefold()
        if (
            "://" in lowered
            or lowered.startswith(("git+", "hg+", "svn+", "bzr+"))
            or " @ " in lowered
        ):
            raise ManagedEnvironmentError(
                "managed_package_source_unsupported",
                "首期不支持 URL、VCS 或 direct-reference dependency。",
            )
        looks_like_path = any(separator in value for separator in ("/", "\\"))
        if (
            value in {".", ".."}
            or lowered.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".zip"))
            or (looks_like_path and not lowered.endswith(".whl"))
        ):
            raise ManagedEnvironmentError(
                "managed_package_source_unsupported",
                "首期只允许 registry wheel 或已授权的本地 .whl。",
            )
    return arguments


def _only_local_wheels(arguments: Sequence[str]) -> bool:
    specs = [value for value in arguments if not value.startswith("-")]
    return bool(specs) and all(value.casefold().endswith(".whl") for value in specs)


def _venv_python(venv_root: Path) -> Path:
    return venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _tree_usage(root: Path) -> tuple[int, int]:
    root = filesystem_path(root)
    if not root.exists():
        return 0, 0
    files = 0
    byte_count = 0
    for directory, _directories, names in os.walk(root):
        for name in names:
            try:
                stat = (Path(directory) / name).stat()
            except OSError:
                continue
            files += 1
            byte_count += stat.st_size
    return files, byte_count


def _latest_packages_lock(session_root: Path) -> Path | None:
    locks = [path for path in session_root.glob("python-*/packages.lock") if path.is_file()]
    if not locks:
        return None
    return max(locks, key=lambda path: path.stat().st_mtime)


def _byte_bucket(value: int) -> str:
    if value == 0:
        return "zero"
    if value < 10 * 1024 * 1024:
        return "lt_10_mib"
    if value < 100 * 1024 * 1024:
        return "10_to_100_mib"
    if value < 1024 * 1024 * 1024:
        return "100_mib_to_1_gib"
    return "gte_1_gib"


def _duration_bucket(value: float) -> str:
    if value < 0.1:
        return "lt_100ms"
    if value < 1:
        return "100ms_to_1s"
    if value < 5:
        return "1s_to_5s"
    if value < 30:
        return "5s_to_30s"
    return "gte_30s"


def _installed_distributions(venv_root: Path) -> list[dict[str, str]]:
    site_packages = (
        venv_root / "Lib" / "site-packages"
        if os.name == "nt"
        else next((venv_root / "lib").glob("python*/site-packages"), Path())
    )
    values: list[dict[str, str]] = []
    if not site_packages.is_dir():
        return values
    for metadata_path in sorted(site_packages.glob("*.dist-info/METADATA")):
        try:
            metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        name = metadata.get("Name", "").strip()
        version = metadata.get("Version", "").strip()
        if not name or not version:
            continue
        direct_url = metadata_path.parent / "direct_url.json"
        values.append(
            {
                "name": name,
                "version": version,
                "source": "local-wheel" if direct_url.is_file() else "registry-or-base",
            }
        )
    values.sort(key=lambda item: (item["name"].casefold(), item["version"]))
    return values


@contextlib.contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        except FileExistsError:
            try:
                if path.stat().st_mtime < time.time() - LOCK_STALE_SECONDS:
                    path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ManagedEnvironmentError(
                    "managed_environment_prepare_failed",
                    "等待 Python session environment prepare lock 超时。",
                ) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        assert descriptor is not None
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    path = filesystem_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _required_manifest_string(manifest: Mapping[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise ManagedEnvironmentError(
            "managed_environment_unavailable", f"受管 Python identity 缺少 {key}。"
        )
    return value


def _safe_manifest_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ManagedEnvironmentError(
            "managed_environment_unavailable", "受管 Python identity 路径越界。"
        ) from exc
    return candidate


def _identity_digest(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "EXECUTION_ENVIRONMENT_ROOT_ENV",
    "UV_EXECUTABLE_ENV",
    "PythonEnvironmentProvider",
]
