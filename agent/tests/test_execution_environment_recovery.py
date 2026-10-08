from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from agent.execution_environment import (
    EnvironmentStatus,
    ManagedEnvironmentError,
    PythonEnvironmentProvider,
)
from agent.execution_environment import python as python_module
from agent.execution_environment.filesystem import filesystem_path, remove_tree


@pytest.fixture
def provider_factory(tmp_path: Path) -> Callable[[], PythonEnvironmentProvider]:
    uv = shutil.which("uv")
    assert uv is not None

    def create() -> PythonEnvironmentProvider:
        provider = PythonEnvironmentProvider.from_runtime(
            data_root=tmp_path / "environments", uv_executable=Path(uv)
        )
        assert provider.prepare_global().status is EnvironmentStatus.READY
        return provider

    return create


def _invalid_environment(provider: PythonEnvironmentProvider) -> Path:
    environment = provider.prepare_session("recovery-test", provider.collect_health())
    root = environment.writable_roots[0]
    (root / "old-marker").write_text("preserve until publication succeeds", encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["invalidReason"] = "test-invalid-environment"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _assert_python_runs(environment_root: Path) -> None:
    executable = python_module._venv_python(environment_root / "venv")
    result = subprocess.run(
        [str(executable), "-I", "-c", "print('recovered')"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "recovered"


def test_rebuild_succeeds_when_old_environment_cleanup_fails(
    provider_factory: Callable[[], PythonEnvironmentProvider],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = _invalid_environment(provider_factory())
    provider = provider_factory()
    original_remove = remove_tree

    def fail_retired_cleanup(path: Path) -> None:
        if path.name.startswith("retired-"):
            # The replacement must already be usable before any old file is deleted.
            _assert_python_runs(root)
            assert (path / "old-marker").is_file()
            raise PermissionError("simulated locked old dependency")
        original_remove(path)

    with monkeypatch.context() as patch:
        patch.setattr(python_module, "remove_tree", fail_retired_cleanup)
        rebuilt = provider.prepare_session("recovery-test", provider.collect_health())
    assert rebuilt.status is EnvironmentStatus.READY
    assert not (root / "old-marker").exists()
    assert "cleanup deferred" in caplog.text
    assert "manifest_marked_invalid" in caplog.text
    retired = list(provider._layout["staging"].glob("retired-*"))
    assert len(retired) == 1
    assert (retired[0] / "old-marker").is_file()
    # A new provider can use the published environment without repeating the rebuild.
    restored_provider = provider_factory()
    restored = restored_provider.prepare_session(
        "recovery-test", restored_provider.collect_health()
    )
    assert restored.status is EnvironmentStatus.READY
    _assert_python_runs(root)
    os.utime(retired[0], (1, 1))
    assert provider._cleanup_staging() == 1
    assert not retired[0].exists()


@pytest.mark.parametrize("failure_stage", ["publish", "probe", "current_manifest"])
def test_failed_publication_restores_old_environment_and_can_retry(
    provider_factory: Callable[[], PythonEnvironmentProvider],
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    root = _invalid_environment(provider_factory())
    old_manifest = (root / "manifest.json").read_bytes()
    current_manifest = (root.parent / "current.json").read_bytes()
    provider = provider_factory()
    original_replace = os.replace
    original_probe = provider._run_python_probe
    original_write = python_module._atomic_json_write

    def replace(source: Path, destination: Path) -> None:
        if (
            failure_stage == "publish"
            and destination == filesystem_path(root)
            and not source.name.startswith(".recovery-")
        ):
            raise PermissionError("simulated publication failure")
        original_replace(source, destination)

    def probe(executable: Path, *, expect_venv: bool) -> dict[str, object]:
        if failure_stage == "probe" and executable == python_module._venv_python(root / "venv"):
            raise subprocess.CalledProcessError(17, "python probe")
        return original_probe(executable, expect_venv=expect_venv)

    def write(path: Path, payload: dict[str, object]) -> None:
        if failure_stage == "current_manifest" and path.name == "current.json":
            raise PermissionError("simulated manifest publication failure")
        original_write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        patch.setattr(provider, "_run_python_probe", probe)
        patch.setattr(python_module, "_atomic_json_write", write)
        with pytest.raises((PermissionError, ManagedEnvironmentError)):
            provider.prepare_session("recovery-test", provider.collect_health())
    assert (root / "old-marker").is_file()
    assert (root / "manifest.json").read_bytes() == old_manifest
    assert (root.parent / "current.json").read_bytes() == current_manifest
    assert not list(provider._layout["staging"].iterdir())
    retried = provider.prepare_session("recovery-test", provider.collect_health())
    assert retried.status is EnvironmentStatus.READY
    _assert_python_runs(root)


def test_rollback_failure_preserves_old_environment_beyond_staging_gc(
    provider_factory: Callable[[], PythonEnvironmentProvider],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = _invalid_environment(provider_factory())
    provider = provider_factory()
    original_replace = os.replace

    def fail_publication_and_rollback(source: Path, destination: Path) -> None:
        if destination == filesystem_path(root):
            raise PermissionError("simulated blocked destination")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_publication_and_rollback)
        with pytest.raises(PermissionError):
            provider.prepare_session("recovery-test", provider.collect_health())
    backups = list(provider._layout["staging"].glob(".recovery-*"))
    assert len(backups) == 1
    assert (backups[0] / "old-marker").is_file()
    assert "rollback failed" in caplog.text
    os.utime(backups[0], (1, 1))
    assert provider._cleanup_staging() == 0
    assert (backups[0] / "old-marker").is_file()


@pytest.mark.parametrize(
    ("manifest", "reason"),
    [
        ("[]", "manifest_not_object"),
        ("{broken", "JSONDecodeError"),
        (None, "FileNotFoundError"),
        ('{"schemaVersion":0}', "manifest_schema_mismatch"),
    ],
)
def test_invalid_environment_logs_reason_and_rebuilds(
    provider_factory: Callable[[], PythonEnvironmentProvider],
    caplog: pytest.LogCaptureFixture,
    manifest: str | None,
    reason: str,
) -> None:
    provider = provider_factory()
    root = provider._session_environment_root("recovery-test")
    root.mkdir(parents=True)
    if manifest is not None:
        (root / "manifest.json").write_text(manifest, encoding="utf-8")
    rebuilt = provider.prepare_session("recovery-test", provider.collect_health())
    assert rebuilt.status is EnvironmentStatus.READY
    assert reason in caplog.text
    _assert_python_runs(root)


def test_probe_failure_logs_native_details_without_output(
    provider_factory: Callable[[], PythonEnvironmentProvider],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = provider_factory()
    environment = provider.prepare_session("recovery-test", provider.collect_health())

    def fail_probe(executable: Path, *, expect_venv: bool) -> dict[str, object]:
        raise subprocess.CalledProcessError(23, str(executable), stderr="private subprocess output")

    monkeypatch.setattr(provider, "_run_python_probe", fail_probe)
    assert (
        provider._load_valid_session_environment("recovery-test", environment.writable_roots[0])
        is None
    )
    assert "phase=python_probe" in caplog.text
    assert "CalledProcessError" in caplog.text
    assert "returncode=23" in caplog.text
    assert "private subprocess output" not in caplog.text


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path environment rebuild")
def test_rebuild_and_quota_count_include_long_dependency_files(
    provider_factory: Callable[[], PythonEnvironmentProvider],
) -> None:
    root = _invalid_environment(provider_factory())
    count, size = python_module._tree_usage(root)
    dependency = (
        root
        / "venv/Lib/site-packages/lxml/isoschematron/resources/xsl/iso-schematron-xslt1"
        / "iso_schematron_skeleton_for_xslt1.xsl"
    )
    assert dependency.is_relative_to(root)
    assert len(str(dependency)) > 260
    native = filesystem_path(dependency)
    native.parent.mkdir(parents=True)
    native.write_bytes(b"long dependency")
    try:
        assert python_module._tree_usage(root) == (count + 1, size + len(b"long dependency"))
        provider = provider_factory()
        rebuilt = provider.prepare_session("recovery-test", provider.collect_health())
        assert rebuilt.status is EnvironmentStatus.READY
        _assert_python_runs(root)
        assert not native.exists()
        assert not list(provider._layout["staging"].iterdir())
    finally:
        native.unlink(missing_ok=True)
