from __future__ import annotations

import importlib.util
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

PACKAGE_DIR = Path(__file__).resolve().parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _roots(tmp_path: Path, *, repo_name: str = "repo"):  # type: ignore[no-untyped-def]
    paths = _load(f"package_paths_{repo_name}", "paths.py")
    repo = tmp_path / repo_name
    common = tmp_path / "common" / ".git"
    home = tmp_path / "home"
    for path in (repo, common, home):
        path.mkdir(parents=True, exist_ok=True)
    return paths.resolve_package_roots(
        repo,
        state_dir=tmp_path / "state",
        releases_dir=tmp_path / "releases",
        home=home,
        common_git_dir=common,
    )


def test_workspace_gc_expires_old_builds_and_inactive_cache(tmp_path: Path) -> None:
    paths = _load("package_paths_gc", "paths.py")
    gc = _load("package_workspace_gc", "workspace_gc.py")
    roots = _roots(tmp_path)
    old_build = paths.create_build_paths(roots, build_id="20260701T000000Z-aaaaaaaaaaaa-oldbuild")
    new_build = paths.create_build_paths(roots, build_id="20260808T110000Z-bbbbbbbbbbbb-newbuild")
    (old_build.build_dir / "runtime-stage").mkdir(parents=True)
    (old_build.build_dir / "runtime-stage" / "old.bin").write_bytes(b"old")
    new_build.build_dir.mkdir(parents=True)
    (new_build.build_dir / "keep.bin").write_bytes(b"keep")
    (roots.work_root / "build-cache" / "cargo-target").mkdir(parents=True)
    (roots.work_root / "build-cache" / "cargo-target" / "cache.bin").write_bytes(b"cache")
    gc.mark_workspace_used(roots, now=datetime(2026, 7, 1, tzinfo=UTC))

    report = gc.workspace_inventory(
        roots,
        now=datetime(2026, 8, 8, 12, tzinfo=UTC),
        build_max_age=timedelta(hours=48),
        cache_max_age=timedelta(days=7),
        registered_worktree_ids={roots.worktree_id},
    )
    candidates = {(item["kind"], item["build_id"]) for item in report["candidates"]}
    assert ("build", old_build.build_id) in candidates
    assert ("build", new_build.build_id) not in candidates
    assert ("cache", None) in candidates

    result = gc.apply_workspace_gc(roots, report)
    assert result["reclaimedBytes"] == len(b"old") + len(b"cache")
    assert not old_build.build_dir.exists()
    assert new_build.build_dir.is_dir()
    assert not (roots.work_root / "build-cache").exists()


def test_package_workspace_lock_is_repository_scoped_across_worktrees(tmp_path: Path) -> None:
    gc = _load("package_workspace_lock_scope", "workspace_gc.py")
    first = _roots(tmp_path, repo_name="first")
    second = _roots(tmp_path, repo_name="second")
    assert first.worktree_id != second.worktree_id
    assert gc.package_workspace_lock_path(first) == gc.package_workspace_lock_path(second)


def test_disk_headroom_fails_closed_with_cleanup_guidance(tmp_path: Path, monkeypatch) -> None:
    gc = _load("package_workspace_disk", "workspace_gc.py")
    roots = _roots(tmp_path)
    monkeypatch.setattr(
        gc.shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(total=100, used=99, free=1),
    )
    try:
        gc.ensure_package_disk_headroom(roots, minimum_free_bytes=2)
    except RuntimeError as error:
        assert "workspace-gc --apply" in str(error)
    else:
        raise AssertionError("low disk package build must fail closed")


def test_success_cleanup_only_removes_exact_build_directory(tmp_path: Path) -> None:
    paths = _load("package_paths_success_cleanup", "paths.py")
    gc = _load("package_workspace_success_cleanup", "workspace_gc.py")
    roots = _roots(tmp_path)
    build = paths.create_build_paths(roots, build_id="20260808T120000Z-aaaaaaaaaaaa-cleanup")
    other = paths.create_build_paths(roots, build_id="20260808T120100Z-bbbbbbbbbbbb-keep")
    build.build_dir.mkdir(parents=True)
    other.build_dir.mkdir(parents=True)
    gc.cleanup_build_after_success(roots, build.build_id)
    assert not build.build_dir.exists()
    assert other.build_dir.is_dir()
