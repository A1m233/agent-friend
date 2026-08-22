from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from agent.execution_environment import (
    EnvironmentStatus,
    ExecutionEnvironmentManager,
    ManagedEnvironmentError,
    PreparedProviderEnvironment,
    ProviderHealth,
    ProviderIdentity,
    PythonEnvironmentProvider,
    ResolvedManagedCommand,
    session_key_for,
)


class _FakeProvider:
    name = "fake"
    identity = ProviderIdentity(
        provider="fake",
        runtime_version="1",
        identity="a" * 64,
        platform="test",
        architecture="test",
    )

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.global_calls = 0
        self.session_calls = 0
        self.released: list[str] = []
        self._lock = threading.Lock()

    def prepare_global(self) -> ProviderHealth:
        with self._lock:
            self.global_calls += 1
        return ProviderHealth(self.identity, EnvironmentStatus.READY)

    def prepare_session(
        self, session_key: str, health: ProviderHealth
    ) -> PreparedProviderEnvironment:
        assert health.status is EnvironmentStatus.READY
        with self._lock:
            self.session_calls += 1
        if self.delay:
            time.sleep(self.delay)
        return PreparedProviderEnvironment(
            identity=self.identity,
            status=EnvironmentStatus.READY,
            session_key=session_key,
            revision=1,
        )

    def resolve_command(
        self,
        environment: PreparedProviderEnvironment,
        argv: tuple[str, ...],
    ) -> ResolvedManagedCommand | None:
        return None

    def matches_command(self, argv: tuple[str, ...]) -> bool:
        return False

    def record_command_success(
        self, command: ResolvedManagedCommand
    ) -> PreparedProviderEnvironment:
        return command.provider

    def release_session(self, session_key: str) -> None:
        self.released.append(session_key)

    def collect_health(self) -> ProviderHealth:
        return ProviderHealth(self.identity, EnvironmentStatus.READY)

    def collect_diagnostics(self, *, active_session_keys: frozenset[str]) -> dict[str, object]:
        return {"provider": self.name, "status": "ready"}

    def classify_failure(
        self, command: ResolvedManagedCommand, output: str
    ) -> tuple[str, str] | None:
        return None

    def gc(self, *, active_session_keys: frozenset[str]) -> dict[str, int]:
        return {"active": len(active_session_keys)}


def test_manager_prepares_global_and_session_once_under_concurrency() -> None:
    provider = _FakeProvider(delay=0.05)
    manager = ExecutionEnvironmentManager([provider])
    try:
        manager.start()
        futures = [manager.schedule_session("session-secret") for _ in range(8)]
        snapshots = [future.result(timeout=2) for future in futures]
        assert provider.global_calls == 1
        assert provider.session_calls == 1
        assert len({id(snapshot) for snapshot in snapshots}) == 1
        assert snapshots[0].session_key == session_key_for("session-secret")
        assert "session-secret" not in snapshots[0].session_key
    finally:
        manager.close()


def test_manager_fixes_timeout_snapshot_for_current_run_only() -> None:
    provider = _FakeProvider(delay=0.1)
    manager = ExecutionEnvironmentManager([provider], prepare_timeout_seconds=0.01)
    try:
        first = manager.snapshot_before_run("session", "run-1")
        assert first.provider("fake").status is EnvironmentStatus.UNAVAILABLE  # type: ignore[union-attr]
        time.sleep(0.15)
        assert manager.snapshot_for_tool("session", "run-1") is first
        second = manager.snapshot_before_run("session", "run-2", timeout_seconds=1)
        assert second.provider("fake").status is EnvironmentStatus.READY  # type: ignore[union-attr]
    finally:
        manager.close()


def test_manager_pre_turn_snapshot_is_reused_without_tool_time_prepare() -> None:
    provider = _FakeProvider()
    manager = ExecutionEnvironmentManager([provider])
    try:
        prepared = manager.prepare_before_turn("session")
        bound = manager.snapshot_for_tool("session", "run")
        assert bound is prepared
        assert provider.session_calls == 1
    finally:
        manager.close()


def test_manager_refuses_session_delete_while_run_snapshot_is_active() -> None:
    provider = _FakeProvider()
    manager = ExecutionEnvironmentManager([provider])
    try:
        manager.snapshot_before_run("session", "run")
        with pytest.raises(RuntimeError, match="active"):
            manager.delete_session("session")
        manager.release_run("session", "run")
        manager.delete_session("session")
        assert provider.released == [session_key_for("session")]
    finally:
        manager.close()


def test_manager_diagnostics_contains_no_session_identity() -> None:
    provider = _FakeProvider()
    manager = ExecutionEnvironmentManager([provider])
    try:
        manager.prepare_before_turn("private-session-id")
        summary = manager.collect_diagnostics()
        serialized = json.dumps(summary)
        assert summary["status"] == "ready"
        assert "private-session-id" not in serialized
    finally:
        manager.close()


@pytest.fixture(scope="module")
def python_provider(tmp_path_factory: pytest.TempPathFactory) -> PythonEnvironmentProvider:
    uv = shutil.which("uv")
    assert uv is not None
    return PythonEnvironmentProvider.from_runtime(
        data_root=tmp_path_factory.mktemp("managed-python") / "execution-environments",
        uv_executable=Path(uv),
    )


def test_python_provider_prepares_isolated_session_and_reuses_it(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    assert health.status is EnvironmentStatus.READY
    first = python_provider.prepare_session("session-key", health)
    second = python_provider.prepare_session("session-key", health)
    assert first.status is EnvironmentStatus.READY
    assert first.executable_aliases["python"] == second.executable_aliases["python3"]
    assert first.revision == second.revision == 1
    assert first.writable_roots == second.writable_roots
    assert first.executable_aliases["python"] != Path(sys.executable).resolve()
    assert not (first.writable_roots[0] / "venv" / "Scripts" / "pip.exe").exists()

    hostile = dict(first.environment)
    hostile["PYTHONPATH"] = str(first.writable_roots[0] / "hostile")
    sanitized = dict(first.environment)
    completed = subprocess.run(
        [
            str(first.executable_aliases["python"]),
            "-c",
            "import json,os,sys; print(json.dumps({'exe':sys.executable,'pp':os.getenv('PYTHONPATH')}))",
        ],
        env=sanitized,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    assert Path(payload["exe"]).resolve() == first.executable_aliases["python"].resolve()
    assert payload["pp"] is None
    assert hostile["PYTHONPATH"] not in first.environment


def test_python_provider_resolves_alias_without_host_path(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("session-key", health)
    resolved = python_provider.resolve_command(environment, ("python", "-c", "print(1)"))
    assert resolved is not None
    assert Path(resolved.argv[0]).resolve() == environment.executable_aliases["python"].resolve()
    assert resolved.display_argv == ("python", "-c", "print(1)")
    assert resolved.package_operation is False


@pytest.mark.parametrize(
    "argv",
    [
        ("pip", "install", "demo-package"),
        ("pip3", "install", "demo-package"),
        ("python", "-m", "pip", "install", "demo-package"),
        ("uv", "pip", "install", "demo-package"),
    ],
)
def test_python_provider_normalizes_package_aliases(
    python_provider: PythonEnvironmentProvider,
    argv: tuple[str, ...],
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("session-key", health)
    resolved = python_provider.resolve_command(environment, argv)
    assert resolved is not None
    assert Path(resolved.argv[0]).name.casefold() in {"uv", "uv.exe"}
    assert resolved.argv.count("--python") == 1
    assert "--no-build" in resolved.argv
    assert "--only-binary" not in resolved.argv
    assert resolved.argv[resolved.argv.index("--link-mode") + 1] == "copy"
    assert resolved.package_operation is True
    assert resolved.requires_network is True
    assert resolved.requires_write is True
    assert resolved.display_argv == argv


def test_python_provider_read_only_package_queries_cannot_mutate_overlay_or_cache(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("query-session", health)
    resolved = python_provider.resolve_command(environment, ("pip", "list"))
    assert resolved is not None
    assert resolved.requires_write is False
    assert resolved.writable_roots == ()
    assert set(environment.writable_roots).issubset(resolved.read_only_roots)
    assert environment.shared_cache_root in resolved.read_only_roots


def test_python_provider_allows_authorized_local_wheel_without_network(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("session-key", health)
    resolved = python_provider.resolve_command(
        environment,
        ("pip", "install", os.path.abspath("fixture-1.0-py3-none-any.whl")),
    )
    assert resolved is not None
    assert resolved.requires_network is False


def test_python_provider_reports_exact_pinned_requirement_cache_hit(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("cache-session", health)
    assert environment.shared_cache_root is not None
    cached = (
        environment.shared_cache_root
        / "wheels-v5"
        / "index"
        / "pypi"
        / "demo-package"
        / "1.0"
        / "demo.whl"
    )
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"fixture")
    resolved = python_provider.resolve_command(
        environment,
        ("pip", "install", "demo_package==1.0"),
    )
    assert resolved is not None
    assert resolved.cache_hit is True


def test_python_provider_rejects_package_mutation_at_overlay_quota(
    python_provider: PythonEnvironmentProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("quota-session", health)
    monkeypatch.setattr(python_provider, "_overlay_max_bytes", 1)
    with pytest.raises(ManagedEnvironmentError) as error:
        python_provider.resolve_command(environment, ("pip", "install", "demo==1.0"))
    assert error.value.code == "managed_environment_quota_exceeded"


def test_python_provider_commits_package_revision_and_lock(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("package-session", health)
    site_packages = environment.writable_roots[0] / "venv" / "Lib" / "site-packages"
    metadata = site_packages / "demo_package-1.2.3.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo-package\nVersion: 1.2.3\n",
        encoding="utf-8",
    )
    command = python_provider.resolve_command(environment, ("pip", "uninstall", "demo-package"))
    assert command is not None
    updated = python_provider.record_command_success(command)
    assert updated.revision == environment.revision + 1
    lock = json.loads((environment.writable_roots[0] / "packages.lock").read_text("utf-8"))
    assert lock["revision"] == updated.revision
    assert {"name": "demo-package", "version": "1.2.3", "source": "registry-or-base"} in lock[
        "packages"
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ("pip", "install", "git+https://example.invalid/project.git"),
        ("pip", "install", "--index-url", "https://example.invalid/simple", "demo"),
        ("pip", "install", "../source-tree"),
        ("pip", "install", "-r", "requirements.txt"),
        ("pip", "install", "."),
        ("uv", "cache", "clean"),
    ],
)
def test_python_provider_rejects_unsupported_package_sources(
    python_provider: PythonEnvironmentProvider,
    argv: tuple[str, ...],
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("session-key", health)
    if argv[:2] == ("uv", "cache"):
        assert python_provider.resolve_command(environment, argv) is None
        return
    with pytest.raises(ManagedEnvironmentError) as error:
        python_provider.resolve_command(environment, argv)
    assert error.value.code == "managed_package_source_unsupported"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "No solution found when resolving dependencies",
            "managed_package_distribution_unavailable",
        ),
        (
            "Failed to download package: error sending request",
            "managed_package_source_unavailable",
        ),
        ("Hash mismatch for cache entry", "managed_cache_integrity_failed"),
        ("unexpected resolver failure", "managed_package_failed"),
    ],
)
def test_python_provider_classifies_managed_package_failures(
    python_provider: PythonEnvironmentProvider,
    output: str,
    expected: str,
) -> None:
    health = python_provider.prepare_global()
    environment = python_provider.prepare_session("session-key", health)
    command = python_provider.resolve_command(environment, ("pip", "install", "demo==1.0"))
    assert command is not None
    classified = python_provider.classify_failure(command, output)
    assert classified is not None
    assert classified[0] == expected


def test_python_provider_fails_closed_for_unavailable_alias(tmp_path: Path) -> None:
    identity = ProviderIdentity(
        provider="python",
        runtime_version=platform.python_version(),
        identity="f" * 64,
        platform=sys.platform,
        architecture=platform.machine().casefold(),
    )
    provider = PythonEnvironmentProvider(
        data_root=tmp_path,
        base_executable=tmp_path / "missing-python",
        uv_executable=None,
        identity=identity,
    )
    health = provider.prepare_global()
    assert health.status is EnvironmentStatus.UNAVAILABLE
    environment = provider.prepare_session("session-key", health)
    with pytest.raises(ManagedEnvironmentError) as error:
        provider.resolve_command(environment, ("python", "-V"))
    assert error.value.code == "managed_environment_unavailable"


def test_python_provider_accepts_current_packaged_identity_schema_only(
    tmp_path: Path,
) -> None:
    provider_root = tmp_path / "runtime" / "python"
    base_executable = provider_root / "base" / "python.exe"
    uv_executable = provider_root / "bin" / "uv.exe"
    base_executable.parent.mkdir(parents=True)
    uv_executable.parent.mkdir(parents=True)
    base_executable.write_bytes(b"python")
    uv_executable.write_bytes(b"uv")
    identity = ProviderIdentity(
        provider="python",
        runtime_version="3.12.14",
        identity="e" * 64,
        platform="windows" if os.name == "nt" else sys.platform,
        architecture=platform.machine().casefold(),
    )
    manifest = {
        "schemaVersion": 2,
        "provider": "python",
        "files": [
            {
                "path": "base/python.exe",
                "sha256": hashlib.sha256(b"python").hexdigest(),
            }
        ],
    }
    provider = PythonEnvironmentProvider(
        data_root=tmp_path / "data",
        base_executable=base_executable,
        uv_executable=uv_executable,
        base_root=provider_root / "base",
        identity=identity,
        packaged_manifest=manifest,
    )
    provider._validate_packaged_manifest()

    provider._packaged_manifest["schemaVersion"] = 1
    with pytest.raises(ManagedEnvironmentError, match="schema"):
        provider._validate_packaged_manifest()


def test_python_provider_diagnostics_omit_private_environment_details(
    python_provider: PythonEnvironmentProvider,
) -> None:
    health = python_provider.prepare_global()
    python_provider.prepare_session("private-session-key", health)
    summary = python_provider.collect_diagnostics(active_session_keys=frozenset())
    serialized = json.dumps(summary)
    assert summary["provider"] == "python"
    assert "private-session-key" not in serialized
    assert "packages" not in serialized
    assert "site-packages" not in serialized
    assert "Scripts" not in serialized


def test_python_provider_gc_preserves_active_overlay_and_bounds_cache(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    identity = ProviderIdentity(
        provider="python",
        runtime_version=platform.python_version(),
        identity="c" * 64,
        platform=sys.platform,
        architecture=platform.machine().casefold(),
    )
    provider = PythonEnvironmentProvider(
        data_root=tmp_path / "gc",
        base_executable=Path(getattr(sys, "_base_executable", sys.executable)),
        uv_executable=Path(uv),
        identity=identity,
        shared_cache_high_bytes=1,
        shared_cache_target_bytes=0,
        inactive_overlays_high_bytes=1,
    )
    health = provider.prepare_global()
    assert health.status is EnvironmentStatus.READY
    active = provider.prepare_session("active-key", health)
    inactive = provider.prepare_session("inactive-key", health)
    assert inactive.shared_cache_root is not None
    (inactive.shared_cache_root / "cache-entry").write_bytes(b"cache")

    while active.writable_roots[0].stat().st_mtime >= inactive.writable_roots[0].stat().st_mtime:
        time.sleep(0.01)
        os.utime(inactive.writable_roots[0], None)
    first = provider.gc(active_session_keys=frozenset({"active-key"}))
    assert first["overlays_removed"] == 1
    assert active.writable_roots[0].is_dir()
    assert not inactive.writable_roots[0].exists()
    assert (inactive.writable_roots[0].parent / "rebuild-packages.lock").is_file()
    assert (inactive.shared_cache_root / "cache-entry").is_file()
    rebuilt = provider.prepare_session("inactive-key", health)
    assert rebuilt.status is EnvironmentStatus.READY
    assert rebuilt.revision == inactive.revision + 1

    second = provider.gc(active_session_keys=frozenset())
    assert second["shared_cache_pruned_bytes"] >= len(b"cache")
    assert not (inactive.shared_cache_root / "cache-entry").exists()
