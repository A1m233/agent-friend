"""Bounded lifecycle management for package workspaces and incremental caches."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from paths import (
    PackageRoots,
    create_build_paths,
    path_identity,
    safe_cache_rmtree,
    safe_rmtree,
)

DEFAULT_BUILD_MAX_AGE = timedelta(hours=48)
DEFAULT_CACHE_MAX_AGE = timedelta(days=7)
PACKAGE_MIN_FREE_BYTES = 40 * 1024**3
WORKSPACE_STATE_FILE = "workspace-state.json"


@dataclass(frozen=True)
class CleanupCandidate:
    kind: str
    path: str
    worktree_id: str
    build_id: str | None
    bytes: int
    age_seconds: int
    reason: str


def package_workspace_lock_path(roots: PackageRoots) -> Path:
    from build_storage.config import control_root

    return control_root() / "locks" / "package" / f"{roots.repo_id}.lock"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _build_created_at(path: Path) -> datetime:
    parsed = _parse_timestamp(path.name.split("-", maxsplit=1)[0])
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _tree_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        root_path = Path(root)
        for name in files:
            try:
                total += (root_path / name).stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _registered_worktree_ids(repo_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    ids: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            ids.add(path_identity(Path(line.removeprefix("worktree "))))
    return ids


def _workspace_last_used(work_root: Path) -> datetime:
    state_path = work_root / WORKSPACE_STATE_FILE
    if state_path.is_file():
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(str(value["updatedAt"]))
            return updated if updated.tzinfo else updated.replace(tzinfo=UTC)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    build_times = [
        _build_created_at(path)
        for path in work_root.iterdir()
        if path.is_dir() and _parse_timestamp(path.name.split("-", maxsplit=1)[0]) is not None
    ]
    if build_times:
        return max(build_times)
    return datetime.fromtimestamp(work_root.stat().st_mtime, tz=UTC)


def mark_workspace_used(roots: PackageRoots, *, now: datetime | None = None) -> None:
    roots.work_root.mkdir(parents=True, exist_ok=True)
    value = {
        "schemaVersion": 1,
        "repoRoot": str(roots.repo_root),
        "repoId": roots.repo_id,
        "worktreeId": roots.worktree_id,
        "updatedAt": (now or _utc_now()).isoformat(),
    }
    destination = roots.work_root / WORKSPACE_STATE_FILE
    temporary = destination.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def workspace_inventory(
    roots: PackageRoots,
    *,
    now: datetime | None = None,
    build_max_age: timedelta = DEFAULT_BUILD_MAX_AGE,
    cache_max_age: timedelta = DEFAULT_CACHE_MAX_AGE,
    registered_worktree_ids: set[str] | None = None,
) -> dict[str, Any]:
    observed_at = now or _utc_now()
    repo_work_root = roots.state_root / "work" / roots.repo_id
    registered = (
        _registered_worktree_ids(roots.repo_root)
        if registered_worktree_ids is None
        else registered_worktree_ids
    )
    volume_path = _nearest_existing(roots.state_root)
    available_bytes = shutil.disk_usage(volume_path).free
    candidates: list[CleanupCandidate] = []
    workspaces: list[dict[str, Any]] = []
    if repo_work_root.is_dir():
        for work_root in sorted(path for path in repo_work_root.iterdir() if path.is_dir()):
            worktree_id = work_root.name
            last_used = _workspace_last_used(work_root)
            workspace: dict[str, Any] = {
                "worktreeId": worktree_id,
                "registered": worktree_id in registered,
                "lastUsedAt": last_used.isoformat(),
                "builds": [],
                "cache": None,
            }
            for build_dir in sorted(path for path in work_root.iterdir() if path.is_dir()):
                created_at = _parse_timestamp(build_dir.name.split("-", maxsplit=1)[0])
                if created_at is None:
                    continue
                age = max(timedelta(), observed_at - created_at)
                size = _tree_size(build_dir)
                workspace["builds"].append(
                    {
                        "buildId": build_dir.name,
                        "createdAt": created_at.isoformat(),
                        "bytes": size,
                    }
                )
                if age >= build_max_age:
                    candidates.append(
                        CleanupCandidate(
                            kind="build",
                            path=str(build_dir.resolve()),
                            worktree_id=worktree_id,
                            build_id=build_dir.name,
                            bytes=size,
                            age_seconds=int(age.total_seconds()),
                            reason="build-retention-expired",
                        )
                    )
            cache_dir = work_root / "build-cache"
            if cache_dir.is_dir():
                age = max(timedelta(), observed_at - last_used)
                size = _tree_size(cache_dir)
                workspace["cache"] = {"path": str(cache_dir), "bytes": size}
                if age >= cache_max_age:
                    candidates.append(
                        CleanupCandidate(
                            kind="cache",
                            path=str(cache_dir.resolve()),
                            worktree_id=worktree_id,
                            build_id=None,
                            bytes=size,
                            age_seconds=int(age.total_seconds()),
                            reason=(
                                "unregistered-worktree-cache-expired"
                                if worktree_id not in registered
                                else "inactive-worktree-cache-expired"
                            ),
                        )
                    )
            workspaces.append(workspace)
    return {
        "schemaVersion": 1,
        "observedAt": observed_at.isoformat(),
        "sizeAccounting": "logical-file-bytes",
        "repoId": roots.repo_id,
        "currentWorktreeId": roots.worktree_id,
        "repoWorkRoot": str(repo_work_root.resolve(strict=False)),
        "policy": {
            "buildMaxAgeHours": build_max_age.total_seconds() / 3600,
            "cacheMaxAgeDays": cache_max_age.total_seconds() / 86400,
            "minimumFreeBytesBeforeBuild": PACKAGE_MIN_FREE_BYTES,
        },
        "disk": {
            "volumePath": str(volume_path),
            "availableBytes": available_bytes,
            "buildAllowed": available_bytes >= PACKAGE_MIN_FREE_BYTES,
        },
        "workspaces": workspaces,
        "candidates": [asdict(candidate) for candidate in candidates],
        "reclaimableBytes": sum(candidate.bytes for candidate in candidates),
    }


def _roots_for_worktree(roots: PackageRoots, worktree_id: str) -> PackageRoots:
    return replace(
        roots,
        worktree_id=worktree_id,
        work_root=roots.state_root / "work" / roots.repo_id / worktree_id,
    )


def apply_workspace_gc(roots: PackageRoots, report: dict[str, Any]) -> dict[str, Any]:
    deleted: list[dict[str, Any]] = []
    for raw in report.get("candidates", []):
        if not isinstance(raw, dict):
            raise RuntimeError("invalid workspace cleanup candidate")
        worktree_id = str(raw.get("worktree_id", ""))
        candidate_roots = _roots_for_worktree(roots, worktree_id)
        kind = str(raw.get("kind", ""))
        if kind == "build":
            build_id = str(raw.get("build_id", ""))
            paths = create_build_paths(candidate_roots, build_id=build_id)
            if paths.build_dir.resolve(strict=False) != Path(str(raw["path"])).resolve(
                strict=False
            ):
                raise RuntimeError("workspace build cleanup path mismatch")
            safe_rmtree(paths.build_dir, build_paths=paths)
        elif kind == "cache":
            cache_root = candidate_roots.work_root / "build-cache"
            if cache_root.resolve(strict=False) != Path(str(raw["path"])).resolve(strict=False):
                raise RuntimeError("workspace cache cleanup path mismatch")
            if cache_root.is_dir():
                for child in list(cache_root.iterdir()):
                    if not child.is_dir():
                        raise RuntimeError(f"unexpected package cache entry: {child}")
                    paths = create_build_paths(candidate_roots, build_id="gc-safety-context")
                    safe_cache_rmtree(child, build_paths=paths)
                cache_root.rmdir()
        else:
            raise RuntimeError(f"unsupported workspace cleanup kind: {kind}")
        deleted.append(raw)
    return {
        "schemaVersion": 1,
        "deleted": deleted,
        "reclaimedBytes": sum(int(item.get("bytes", 0)) for item in deleted),
    }


def cleanup_build_after_success(roots: PackageRoots, build_id: str) -> None:
    paths = create_build_paths(roots, build_id=build_id)
    from build_storage import Storage
    from build_storage.execution import complete_build
    from build_storage.files import identity

    storage = (
        Storage(roots.repo_root, common=roots.repo_root)
        if not (roots.repo_root / ".git").exists()
        else Storage(roots.repo_root)
    )
    if storage.record_path(identity(paths.build_dir)).exists():
        complete_build(storage, paths.build_dir, remove=True, outcome="passed")
    else:
        safe_rmtree(paths.build_dir, build_paths=paths)


def cleanup_smoke_builds_after_success(roots: PackageRoots, build_report_path: Path) -> list[str]:
    value = json.loads(build_report_path.read_text(encoding="utf-8"))
    baseline = value.get("baseline")
    candidate = value.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise RuntimeError("installer smoke build report is incomplete for workspace cleanup")
    build_ids = [str(baseline.get("buildId", "")), str(candidate.get("buildId", ""))]
    if any(not build_id for build_id in build_ids):
        raise RuntimeError("installer smoke build report lacks build ids for workspace cleanup")
    deleted: list[str] = []
    for build_id in dict.fromkeys(build_ids):
        cleanup_build_after_success(roots, build_id)
        deleted.append(build_id)
    return deleted


def _nearest_existing(path: Path) -> Path:
    candidate = path.resolve(strict=False)
    while not candidate.exists():
        if candidate.parent == candidate:
            raise RuntimeError(f"no existing parent for package state path: {path}")
        candidate = candidate.parent
    return candidate


def ensure_package_disk_headroom(
    roots: PackageRoots,
    *,
    minimum_free_bytes: int = PACKAGE_MIN_FREE_BYTES,
) -> dict[str, int | str]:
    volume_path = _nearest_existing(roots.state_root)
    free = shutil.disk_usage(volume_path).free
    if free < minimum_free_bytes:
        raise RuntimeError(
            "package build requires more free space before creating a workspace: "
            f"available={free}, required={minimum_free_bytes}, state={roots.state_root}. "
            "Run scripts/package/run.* workspace-status and workspace-gc --apply, or "
            "move --state-dir to a volume with enough space."
        )
    return {
        "statePath": str(roots.state_root),
        "availableBytes": free,
        "requiredBytes": minimum_free_bytes,
    }
