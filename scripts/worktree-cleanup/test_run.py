from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).with_name("run.py")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worktree_cleanup", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Worktree Test")
    _git(repo, "config", "user.email", "worktree@example.invalid")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _by_path(report: dict[str, object], path: Path) -> dict[str, object]:
    entries = report["worktrees"]
    assert isinstance(entries, list)
    return next(item for item in entries if item["path"] == str(path.resolve()))


def test_clean_branch_with_unique_commits_is_removable_and_branch_is_preserved(
    tmp_path: Path,
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    worktree = tmp_path / "feature"
    _git(repo, "worktree", "add", "-b", "feature/unique", str(worktree))
    (worktree / "feature.txt").write_text("unique\n", encoding="utf-8")
    _git(worktree, "add", "feature.txt")
    _git(worktree, "commit", "-m", "unique")

    report = module.inventory(repo, main_ref="main", codex_root=tmp_path, include_sizes=False)
    item = _by_path(report, worktree)
    assert item["category"] == "removable-clean"
    assert item["integration"] == "unique-commits"

    module.remove_worktree(
        repo,
        path=worktree,
        main_ref="main",
        expected_head=str(item["head"]),
        expected_fingerprint=str(item["fingerprint"]),
        discard_redundant_dirty=False,
        codex_root=tmp_path,
    )
    assert not worktree.exists()
    assert _git(repo, "show-ref", "--verify", "refs/heads/feature/unique")


def test_remove_finishes_exact_residual_after_git_unregisters_on_windows_style_failure(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    worktree = tmp_path / "task-id" / repo.name
    worktree.parent.mkdir()
    _git(repo, "worktree", "add", "-b", "feature/partial", str(worktree))
    report = module.inventory(repo, main_ref="main", codex_root=tmp_path, include_sizes=False)
    item = _by_path(report, worktree)
    real_git = module._git

    def partial_remove(cwd: Path, *args: str, **kwargs):
        if args[:2] == ("worktree", "remove"):
            result = real_git(cwd, *args, **kwargs)
            worktree.mkdir(parents=True)
            (worktree / "residual.bin").write_bytes(b"build artifact")
            return subprocess.CompletedProcess(result.args, 1, result.stdout, "simulated residual")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(module, "_git", partial_remove)
    module.remove_worktree(
        repo,
        path=worktree,
        main_ref="main",
        expected_head=str(item["head"]),
        expected_fingerprint=str(item["fingerprint"]),
        discard_redundant_dirty=False,
        codex_root=tmp_path,
    )
    assert not worktree.exists()
    assert not worktree.parent.exists()
    assert _git(repo, "show-ref", "--verify", "refs/heads/feature/partial")


def test_exact_residual_removal_tolerates_only_a_locked_empty_root(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load()
    codex_root = tmp_path / "codex"
    target = codex_root / "task-id" / "repo"
    target.mkdir(parents=True)
    (target / "artifact.bin").write_bytes(b"artifact")

    def leave_locked_empty_root(path: Path) -> None:
        (path / "artifact.bin").unlink()
        raise PermissionError(32, "simulated locked root", str(path))

    monkeypatch.setattr(module.shutil, "rmtree", leave_locked_empty_root)
    assert module._remove_exact_codex_tree(target, codex_root) is False
    assert target.exists()
    assert not any(target.iterdir())


def test_exact_residual_removal_accepts_direct_codex_worktree_layout(
    tmp_path: Path,
) -> None:
    module = _load()
    codex_root = tmp_path / "codex"
    target = codex_root / "merge-recovery-agent-friend"
    target.mkdir(parents=True)
    (target / "artifact.bin").write_bytes(b"artifact")

    assert module._remove_exact_codex_tree(target, codex_root) is True
    assert not target.exists()
    assert codex_root.exists()


def test_unreferenced_detached_head_is_blocked(tmp_path: Path) -> None:
    module = _load()
    repo = _repo(tmp_path)
    worktree = tmp_path / "detached"
    _git(repo, "worktree", "add", "--detach", str(worktree), "main")
    (worktree / "detached.txt").write_text("unique\n", encoding="utf-8")
    _git(worktree, "add", "detached.txt")
    _git(worktree, "commit", "-m", "detached unique")

    report = module.inventory(repo, main_ref="main", codex_root=tmp_path, include_sizes=False)
    item = _by_path(report, worktree)
    assert item["category"] == "blocked-unreferenced-detached-head"
    assert item["removable"] is False


def test_dirty_tracked_content_that_matches_main_requires_explicit_discard(
    tmp_path: Path,
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    worktree = tmp_path / "redundant"
    _git(repo, "worktree", "add", "-b", "feature/redundant", str(worktree))
    (repo / "tracked.txt").write_text("main version\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "main version")
    (worktree / "tracked.txt").write_text("main version\n", encoding="utf-8")

    report = module.inventory(repo, main_ref="main", codex_root=tmp_path, include_sizes=False)
    item = _by_path(report, worktree)
    assert item["category"] == "review-redundant-dirty"
    assert item["requires_force"] is True


def test_dirty_content_not_in_main_is_blocked(tmp_path: Path) -> None:
    module = _load()
    repo = _repo(tmp_path)
    worktree = tmp_path / "dirty"
    _git(repo, "worktree", "add", "-b", "feature/dirty", str(worktree))
    (worktree / "tracked.txt").write_text("local only\n", encoding="utf-8")

    report = module.inventory(repo, main_ref="main", codex_root=tmp_path, include_sizes=False)
    item = _by_path(report, worktree)
    assert item["category"] == "blocked-dirty"
    assert item["removable"] is False


def test_empty_unregistered_codex_directory_can_be_removed(tmp_path: Path) -> None:
    module = _load()
    repo = _repo(tmp_path)
    codex_root = tmp_path / "codex"
    orphan = codex_root / "task-id" / repo.name
    orphan.mkdir(parents=True)

    report = module.inventory(repo, main_ref="main", codex_root=codex_root, include_sizes=False)
    item = _by_path(report, orphan)
    assert item["category"] == "removable-empty-codex-directory"
    module.remove_worktree(
        repo,
        path=orphan,
        main_ref="main",
        expected_head="",
        expected_fingerprint=str(item["fingerprint"]),
        discard_redundant_dirty=False,
        codex_root=codex_root,
    )
    assert not orphan.exists()
    assert not orphan.parent.exists()
