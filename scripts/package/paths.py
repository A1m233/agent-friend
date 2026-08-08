"""Resolve package build state and release paths outside source worktrees."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

STATE_ENV = "AGENT_FRIEND_PACKAGE_STATE_DIR"
RELEASES_ENV = "AGENT_FRIEND_RELEASES_DIR"


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _identity(value: Path) -> str:
    normalized = os.path.normcase(str(_resolved(value)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _git_path(repo_root: Path, *args: str) -> Path:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    value = Path(result.stdout.strip())
    if not value.is_absolute():
        value = repo_root / value
    return _resolved(value)


@dataclass(frozen=True)
class PackageRoots:
    repo_root: Path
    state_root: Path
    cache_dir: Path
    work_root: Path
    releases_root: Path
    repo_id: str
    worktree_id: str


@dataclass(frozen=True)
class BuildPaths:
    roots: PackageRoots
    build_id: str
    build_dir: Path
    pyinstaller_dir: Path
    pyinstaller_cache_dir: Path
    runtime_stage: Path
    payload_stage: Path
    frontend_dist: Path
    cargo_target_dir: Path
    generated_config_dir: Path
    publish_staging_dir: Path


def resolve_package_roots(
    repo_root: Path,
    *,
    state_dir: Path | None = None,
    releases_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    local_appdata: Path | None = None,
    common_git_dir: Path | None = None,
) -> PackageRoots:
    """Resolve stable roots without creating them."""

    env = os.environ if environ is None else environ
    repo = _resolved(repo_root)
    user_home = _resolved(home or Path.home())
    local = _resolved(
        local_appdata or Path(env.get("LOCALAPPDATA", user_home / "AppData" / "Local"))
    )
    state = _resolved(state_dir or Path(env.get(STATE_ENV, local / "agent-friend" / "packaging")))
    releases = _resolved(
        releases_dir or Path(env.get(RELEASES_ENV, user_home / "agent-friend-releases"))
    )

    for label, value in (("package state", state), ("package releases", releases)):
        if value == repo or _is_within(value, repo):
            raise RuntimeError(f"{label} directory must be outside the repository: {value}")
        if value == user_home or value == Path(value.anchor):
            raise RuntimeError(f"refusing unsafe {label} directory: {value}")
    if state == releases or _is_within(releases, state) or _is_within(state, releases):
        raise RuntimeError("package state and releases directories must be separate")

    common = (
        _resolved(common_git_dir)
        if common_git_dir
        else _git_path(repo, "rev-parse", "--git-common-dir")
    )
    repo_id = _identity(common)
    worktree_id = _identity(repo)
    work_root = state / "work" / repo_id / worktree_id
    return PackageRoots(
        repo_root=repo,
        state_root=state,
        cache_dir=state / "cache",
        work_root=work_root,
        releases_root=releases,
        repo_id=repo_id,
        worktree_id=worktree_id,
    )


def new_build_id(commit: str, *, now: datetime | None = None, suffix: str | None = None) -> str:
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    short_commit = commit[:12] if commit else "unknown"
    random_suffix = suffix or uuid.uuid4().hex[:8]
    return f"{timestamp}-{short_commit}-{random_suffix}"


def create_build_paths(roots: PackageRoots, *, build_id: str) -> BuildPaths:
    if not build_id or any(character in build_id for character in "\\/:"):
        raise RuntimeError(f"invalid build id: {build_id!r}")
    build_dir = _resolved(roots.work_root / build_id)
    if not _is_within(build_dir, _resolved(roots.work_root)):
        raise RuntimeError(f"build directory escaped work root: {build_dir}")
    return BuildPaths(
        roots=roots,
        build_id=build_id,
        build_dir=build_dir,
        pyinstaller_dir=build_dir / "pyinstaller",
        pyinstaller_cache_dir=roots.work_root / "build-cache" / "pyinstaller",
        runtime_stage=build_dir / "runtime-stage",
        payload_stage=build_dir / "payload-stage",
        frontend_dist=build_dir / "frontend-dist",
        cargo_target_dir=roots.work_root / "build-cache" / "cargo-target",
        generated_config_dir=build_dir / "config",
        publish_staging_dir=build_dir / "publish-staging",
    )


def load_build_paths(roots: PackageRoots, *, build_id: str) -> BuildPaths:
    paths = create_build_paths(roots, build_id=build_id)
    if not paths.build_dir.is_dir():
        raise RuntimeError(f"package build id not found for this worktree: {build_id}")
    return paths


def safe_rmtree(path: Path, *, build_paths: BuildPaths) -> None:
    target = _resolved(path)
    build_dir = _resolved(build_paths.build_dir)
    work_root = _resolved(build_paths.roots.work_root)
    protected = {
        _resolved(build_paths.roots.repo_root),
        _resolved(build_paths.roots.state_root),
        _resolved(build_paths.roots.releases_root),
        _resolved(Path.home()),
        Path(target.anchor),
    }
    if (
        target in protected
        or not _is_within(target, build_dir)
        or not _is_within(build_dir, work_root)
    ):
        raise RuntimeError(f"refusing to delete outside current package build: {target}")
    if target.exists():
        shutil.rmtree(target)


def safe_cache_rmtree(path: Path, *, build_paths: BuildPaths) -> None:
    """Delete one shared build-cache child without widening general cleanup scope."""

    target = _resolved(path)
    cache_root = _resolved(build_paths.roots.work_root / "build-cache")
    protected = {
        cache_root,
        _resolved(build_paths.roots.work_root),
        _resolved(build_paths.roots.state_root),
        _resolved(build_paths.roots.repo_root),
        _resolved(Path.home()),
        Path(target.anchor),
    }
    if target in protected or not _is_within(target, cache_root):
        raise RuntimeError(f"refusing to delete outside package build cache: {target}")
    if target.exists():
        shutil.rmtree(target)
