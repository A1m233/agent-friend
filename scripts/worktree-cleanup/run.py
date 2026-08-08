#!/usr/bin/env python3
"""Inventory and remove agent-friend Git/Codex worktrees with fail-closed checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))
from runtime_guard import list_processes, process_start_id_direct  # noqa: E402


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(parent))
    except ValueError:
        return False
    return True


def _git(
    cwd: Path,
    *args: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
    )


def _git_text(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def _remove_exact_codex_tree(target: Path, codex_root: Path) -> bool:
    target = _resolved(target)
    codex_root = _resolved(codex_root)
    nested_layout = target.parent.parent == codex_root
    direct_layout = target.parent == codex_root
    if not nested_layout and not direct_layout:
        raise RuntimeError(f"refusing to remove path outside the exact Codex layout: {target}")
    if (target / ".git").exists():
        raise RuntimeError(f"refusing fallback removal while Git metadata remains: {target}")
    try:
        shutil.rmtree(target)
    except OSError as error:
        if target.is_dir() and not any(target.iterdir()):
            print(f"warning: worktree data removed but empty root is still locked: {target}")
            return False
        raise RuntimeError(
            f"failed to remove residual worktree directory {target}: {error}"
        ) from error
    if nested_layout and not any(target.parent.iterdir()):
        target.parent.rmdir()
    return True


@dataclass(frozen=True)
class RegisteredWorktree:
    path: Path
    head: str
    branch_ref: str | None
    detached: bool
    locked: bool
    prunable: bool


@dataclass(frozen=True)
class WorktreeAssessment:
    path: str
    head: str | None
    branch: str | None
    category: str
    removable: bool
    requires_force: bool
    integration: str | None
    dirty: bool | None
    tracked_changes: bool | None
    untracked_count: int | None
    working_tree_matches_main: bool | None
    referenced_by: list[str]
    active_reasons: list[str]
    fingerprint: str | None
    logical_bytes: int | None
    unique_bytes: int | None
    notes: list[str]


def _parse_worktrees(repo_root: Path) -> list[RegisteredWorktree]:
    output = _git_text(repo_root, "worktree", "list", "--porcelain")
    records: list[RegisteredWorktree] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        if not current:
            return
        records.append(
            RegisteredWorktree(
                path=_resolved(Path(current["worktree"])),
                head=str(current.get("HEAD", "")),
                branch_ref=current.get("branch"),
                detached=bool(current.get("detached")),
                locked=bool(current.get("locked")),
                prunable=bool(current.get("prunable")),
            )
        )
        current.clear()

    for line in [*output.splitlines(), ""]:
        if not line:
            flush()
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    return records


def _branch_name(branch_ref: str | None) -> str | None:
    prefix = "refs/heads/"
    if branch_ref and branch_ref.startswith(prefix):
        return branch_ref.removeprefix(prefix)
    return branch_ref


def _untracked_paths(path: Path) -> list[str]:
    raw = _git(path, "ls-files", "--others", "--exclude-standard", "-z", text=False).stdout
    return [item.decode("utf-8", errors="surrogateescape") for item in raw.split(b"\0") if item]


def _dirty_fingerprint(path: Path, untracked: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(_git(path, "diff", "--binary", "HEAD", "--", text=False).stdout)
    for relative in sorted(untracked):
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        candidate = path / relative
        if candidate.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(candidate).encode("utf-8", errors="surrogateescape"))
        elif candidate.is_file():
            digest.update(b"file\0")
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"other\0")
    return digest.hexdigest()


def _git_differs(path: Path, *args: str) -> bool:
    result = _git(path, "diff", "--quiet", *args, check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"git diff failed for {path}: {result.stderr.strip()}")
    return result.returncode == 1


def _integration(path: Path, head: str, main_ref: str) -> str:
    ancestor = _git(path, "merge-base", "--is-ancestor", head, main_ref, check=False)
    if ancestor.returncode == 0:
        return "included-by-ancestry"
    if ancestor.returncode not in {0, 1}:
        return "unknown"
    cherry = _git_text(path, "cherry", main_ref, head)
    rows = [line for line in cherry.splitlines() if line.strip()]
    if rows and all(line.startswith("-") for line in rows):
        return "patch-equivalent-in-main"
    return "unique-commits"


def _containing_refs(path: Path, head: str) -> list[str]:
    output = _git_text(
        path,
        "for-each-ref",
        "--contains",
        head,
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
    )
    return sorted(line for line in output.splitlines() if line)


def _dev_runtime_path() -> Path:
    system = platform.system().lower()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
        return Path(base) / "agent-friend" / "dev-runtime" / "lease.json"
    if system == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "agent-friend"
            / "dev-runtime"
            / "lease.json"
        )
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state / "agent-friend" / "dev-runtime" / "lease.json"


def _active_dev_worktree() -> Path | None:
    lease_path = _dev_runtime_path()
    if not lease_path.is_file():
        return None
    try:
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        pid = int(lease.get("ownerPid", 0))
        worktree = lease.get("worktree")
        expected_start = str(lease.get("ownerStartId", ""))
        actual_start = process_start_id_direct(pid) if pid > 0 else ""
        if (
            pid > 0
            and worktree
            and actual_start
            and (not expected_start or expected_start == actual_start)
        ):
            return _resolved(Path(str(worktree)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def _process_references(paths: list[Path]) -> dict[Path, list[str]]:
    result = {path: [] for path in paths}
    lowered = {path: os.path.normcase(str(path)) for path in paths}
    ignored_pids = {os.getpid()}
    processes = list_processes()
    by_pid = {process.pid: process for process in processes}
    cursor = by_pid.get(os.getpid())
    while cursor and cursor.ppid > 0 and cursor.ppid not in ignored_pids:
        ignored_pids.add(cursor.ppid)
        cursor = by_pid.get(cursor.ppid)
    for process in processes:
        if process.pid in ignored_pids:
            continue
        normalized_command = os.path.normcase(process.command)
        for path in paths:
            if lowered[path] in normalized_command:
                result[path].append(f"process:{process.pid}:{process.name or 'unknown'}")
    return result


def _tree_sizes(path: Path) -> tuple[int, int]:
    logical = 0
    unique = 0
    identities: set[tuple[int, int]] = set()
    for root, directories, files in os.walk(path):
        directories[:] = [name for name in directories if name != ".git"]
        root_path = Path(root)
        for name in files:
            candidate = root_path / name
            try:
                stat = candidate.stat(follow_symlinks=False)
            except OSError:
                continue
            logical += stat.st_size
            identity = (stat.st_dev, stat.st_ino)
            if identity not in identities:
                identities.add(identity)
                unique += stat.st_size
    return logical, unique


def _assess_registered(
    item: RegisteredWorktree,
    *,
    current_path: Path,
    primary_path: Path,
    codex_root: Path,
    main_ref: str,
    process_reasons: list[str],
    active_dev: Path | None,
    include_sizes: bool,
) -> WorktreeAssessment:
    path = item.path
    branch = _branch_name(item.branch_ref)
    notes: list[str] = []
    active_reasons = list(process_reasons)
    if path == current_path:
        active_reasons.append("current-worktree")
    if path == primary_path:
        active_reasons.append("primary-worktree")
    if not _is_within(path, codex_root):
        active_reasons.append("outside-codex-root")
    if branch == main_ref or item.branch_ref == f"refs/heads/{main_ref}":
        active_reasons.append("main-worktree")
    if active_dev == path:
        active_reasons.append("active-dev-runtime-lease")
    if item.locked:
        active_reasons.append("git-worktree-locked")
    if item.prunable or not path.is_dir():
        return WorktreeAssessment(
            path=str(path),
            head=item.head or None,
            branch=branch,
            category="stale-git-metadata",
            removable=False,
            requires_force=False,
            integration=None,
            dirty=None,
            tracked_changes=None,
            untracked_count=None,
            working_tree_matches_main=None,
            referenced_by=[],
            active_reasons=active_reasons,
            fingerprint=None,
            logical_bytes=None,
            unique_bytes=None,
            notes=["目录缺失或 Git 标记为 prunable；本工具不自动 prune 元数据。"],
        )

    untracked = _untracked_paths(path)
    tracked_changes = _git_differs(path, "HEAD", "--")
    dirty = tracked_changes or bool(untracked)
    fingerprint = _dirty_fingerprint(path, untracked)
    integration = _integration(path, item.head, main_ref)
    refs = _containing_refs(path, item.head)
    working_matches_main = False
    if dirty and not untracked:
        working_matches_main = not _git_differs(path, main_ref, "--")

    detached_unreferenced = item.detached and not refs and integration == "unique-commits"
    category = "removable-clean"
    removable = True
    requires_force = False
    if active_reasons:
        category = "protected-active"
        removable = False
    elif detached_unreferenced:
        category = "blocked-unreferenced-detached-head"
        removable = False
        notes.append("detached HEAD 含未被任何分支或远端引用的独有提交。")
    elif dirty and working_matches_main:
        category = "review-redundant-dirty"
        removable = True
        requires_force = True
        notes.append("已跟踪工作区内容与主干一致，但删除仍会丢弃本地 dirty 状态。")
    elif dirty:
        category = "blocked-dirty"
        removable = False
        if untracked:
            notes.append("存在未跟踪文件，不能证明内容已由主干保存。")
        else:
            notes.append("dirty 内容与主干不同。")
    elif integration == "unique-commits" and branch:
        notes.append("分支含主干尚未包含的提交；移除 worktree 不会删除该分支。")
    elif integration in {"included-by-ancestry", "patch-equivalent-in-main"}:
        notes.append("HEAD 内容已由主干包含。")

    logical_bytes = unique_bytes = None
    if include_sizes:
        logical_bytes, unique_bytes = _tree_sizes(path)
    return WorktreeAssessment(
        path=str(path),
        head=item.head,
        branch=branch,
        category=category,
        removable=removable,
        requires_force=requires_force,
        integration=integration,
        dirty=dirty,
        tracked_changes=tracked_changes,
        untracked_count=len(untracked),
        working_tree_matches_main=working_matches_main,
        referenced_by=refs,
        active_reasons=sorted(set(active_reasons)),
        fingerprint=fingerprint,
        logical_bytes=logical_bytes,
        unique_bytes=unique_bytes,
        notes=notes,
    )


def _default_codex_root() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "worktrees"


def _orphan_codex_dirs(
    codex_root: Path,
    *,
    repo_name: str,
    registered: set[Path],
    include_sizes: bool,
) -> list[WorktreeAssessment]:
    if not codex_root.is_dir():
        return []
    result: list[WorktreeAssessment] = []
    for candidate in sorted(codex_root.glob(f"*/{repo_name}")):
        resolved = _resolved(candidate)
        if resolved in registered:
            continue
        is_empty = resolved.is_dir() and not any(resolved.iterdir())
        fingerprint = hashlib.sha256(b"empty-codex-directory-v1").hexdigest() if is_empty else None
        logical_bytes = unique_bytes = None
        if include_sizes:
            logical_bytes, unique_bytes = _tree_sizes(resolved)
        result.append(
            WorktreeAssessment(
                path=str(resolved),
                head=None,
                branch=None,
                category=(
                    "removable-empty-codex-directory"
                    if is_empty
                    else "blocked-unregistered-codex-directory"
                ),
                removable=is_empty,
                requires_force=False,
                integration=None,
                dirty=None,
                tracked_changes=None,
                untracked_count=None,
                working_tree_matches_main=None,
                referenced_by=[],
                active_reasons=[],
                fingerprint=fingerprint,
                logical_bytes=logical_bytes,
                unique_bytes=unique_bytes,
                notes=(
                    ["目录不在 git worktree list 中且为空，只允许精确 rmdir。"]
                    if is_empty
                    else ["目录不在 git worktree list 中，无法可靠证明是否含未保存内容。"]
                ),
            )
        )
    return result


def inventory(
    repo_root: Path,
    *,
    main_ref: str,
    codex_root: Path,
    include_sizes: bool,
) -> dict[str, Any]:
    root = _resolved(Path(_git_text(repo_root, "rev-parse", "--show-toplevel")))
    _git_text(root, "rev-parse", "--verify", main_ref)
    current = root
    items = _parse_worktrees(root)
    primary_path = items[0].path
    process_map = _process_references([item.path for item in items])
    active_dev = _active_dev_worktree()
    assessments = [
        _assess_registered(
            item,
            current_path=current,
            primary_path=primary_path,
            codex_root=_resolved(codex_root),
            main_ref=main_ref,
            process_reasons=process_map[item.path],
            active_dev=active_dev,
            include_sizes=include_sizes,
        )
        for item in items
    ]
    registered = {item.path for item in items}
    assessments.extend(
        _orphan_codex_dirs(
            _resolved(codex_root),
            repo_name=root.name,
            registered=registered,
            include_sizes=include_sizes,
        )
    )
    return {
        "schemaVersion": 1,
        "repository": str(root),
        "commonGitDir": _git_text(root, "rev-parse", "--git-common-dir"),
        "mainRef": main_ref,
        "mainHead": _git_text(root, "rev-parse", main_ref),
        "codexRoot": str(_resolved(codex_root)),
        "worktrees": [asdict(item) for item in assessments],
    }


def _print_inventory(report: dict[str, Any]) -> None:
    print(f"repository: {report['repository']}")
    print(f"main: {report['mainRef']} ({str(report['mainHead'])[:12]})")
    print()
    for item in report["worktrees"]:
        size = ""
        if item["unique_bytes"] is not None:
            size = f" unique={item['unique_bytes'] / (1024**3):.2f}GiB"
        branch = item["branch"] or "detached/unregistered"
        print(f"[{item['category']}] {item['path']}")
        print(
            f"  branch={branch} head={(item['head'] or '-')[:12]} "
            f"integration={item['integration'] or '-'} dirty={item['dirty']}{size}"
        )
        if item["active_reasons"]:
            print("  protected: " + ", ".join(item["active_reasons"]))
        for note in item["notes"]:
            print(f"  note: {note}")


def remove_worktree(
    repo_root: Path,
    *,
    path: Path,
    main_ref: str,
    expected_head: str,
    expected_fingerprint: str,
    discard_redundant_dirty: bool,
    codex_root: Path,
) -> None:
    root = _resolved(Path(_git_text(repo_root, "rev-parse", "--show-toplevel")))
    target = _resolved(path)
    matches = [item for item in _parse_worktrees(root) if item.path == target]
    if not matches:
        expected_empty_fingerprint = hashlib.sha256(b"empty-codex-directory-v1").hexdigest()
        codex = _resolved(codex_root)
        if (
            not _is_within(target, codex)
            or target.parent.parent != codex
            or target.name != root.name
            or not target.is_dir()
            or any(target.iterdir())
            or expected_fingerprint != expected_empty_fingerprint
        ):
            raise RuntimeError("unregistered target is not an exact empty Codex project directory")
        target.rmdir()
        if target.parent.parent == codex and not any(target.parent.iterdir()):
            target.parent.rmdir()
        print(f"removed empty Codex directory: {target}")
        return
    if len(matches) != 1:
        raise RuntimeError("target is not exactly one registered Git worktree")
    process_map = _process_references([target])
    assessment = _assess_registered(
        matches[0],
        current_path=root,
        primary_path=_parse_worktrees(root)[0].path,
        codex_root=_resolved(codex_root),
        main_ref=main_ref,
        process_reasons=process_map[target],
        active_dev=_active_dev_worktree(),
        include_sizes=False,
    )
    if assessment.head != expected_head or assessment.fingerprint != expected_fingerprint:
        raise RuntimeError("worktree changed after inventory; run inventory again")
    if not assessment.removable:
        raise RuntimeError(f"worktree is not removable: {assessment.category}")
    command = ["worktree", "remove"]
    if assessment.requires_force:
        if not discard_redundant_dirty:
            raise RuntimeError("redundant dirty worktree requires --discard-redundant-dirty")
        command.append("--force")
    command.extend(["--", str(target)])
    result = _git(root, *command, check=False)
    if result.returncode != 0:
        still_registered = any(_resolved(item.path) == target for item in _parse_worktrees(root))
        if still_registered:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
            raise RuntimeError(f"git worktree remove failed: {detail}")
        if target.exists():
            # On Windows Git can unregister the worktree before failing to remove
            # a large directory tree. The target was fully assessed immediately
            # above, so finish only that exact now-unregistered Codex directory.
            fully_removed = _remove_exact_codex_tree(target, codex_root)
            if fully_removed:
                print(
                    "git unregistered the worktree but left files behind; "
                    "removed exact residual directory"
                )
    print(f"removed worktree: {target}")
    if assessment.branch:
        print(f"preserved branch: {assessment.branch}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--main-ref", default="main")
    parser.add_argument("--codex-root", type=Path, default=_default_codex_root())
    sub = parser.add_subparsers(dest="command", required=True)
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.add_argument("--json", action="store_true")
    inventory_parser.add_argument("--sizes", action="store_true")
    remove_parser = sub.add_parser("remove")
    remove_parser.add_argument("--path", type=Path, required=True)
    remove_parser.add_argument("--expected-head", default="")
    remove_parser.add_argument("--expected-fingerprint", required=True)
    remove_parser.add_argument("--discard-redundant-dirty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "inventory":
        report = inventory(
            args.repo_root,
            main_ref=args.main_ref,
            codex_root=args.codex_root,
            include_sizes=args.sizes,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_inventory(report)
        return 0
    if args.command == "remove":
        remove_worktree(
            args.repo_root,
            path=args.path,
            main_ref=args.main_ref,
            expected_head=args.expected_head,
            expected_fingerprint=args.expected_fingerprint,
            discard_redundant_dirty=args.discard_redundant_dirty,
            codex_root=args.codex_root,
        )
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
