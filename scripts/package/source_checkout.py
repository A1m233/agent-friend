"""Materialize the caller's Git index and working files in a private build checkout."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], env=env, encoding="utf-8"
    ).strip()


@contextmanager
def package_checkout(repo: Path) -> Iterator[Path]:
    head = _git(repo, "rev-parse", "HEAD")
    staged = _git(repo, "write-tree")
    with tempfile.TemporaryDirectory(prefix="af-pkg-") as folder:
        scratch = Path(folder).resolve()
        index_env = {**os.environ, "GIT_INDEX_FILE": str(scratch / "index")}
        _git(repo, "read-tree", staged, env=index_env)
        _git(repo, "add", "--all", "--", ".", env=index_env)
        files = _git(repo, "write-tree", env=index_env)
        checkout = scratch / "src"
        _git(repo, "worktree", "add", "--detach", "--no-checkout", str(checkout), head)
        try:
            _git(checkout, "read-tree", "--reset", "-u", files)
            _git(checkout, "read-tree", staged)
            yield checkout
        finally:
            _git(repo, "worktree", "remove", "--force", str(checkout))
