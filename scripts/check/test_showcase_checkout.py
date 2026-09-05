from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_PATH = ROOT / "scripts" / "showcase-snapshot" / "snapshot.py"


def _load_snapshot_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_friend_showcase_snapshot", RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUN = _load_snapshot_module()


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _files(root: Path, *, include_git: bool = False) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and (include_git or ".git" not in path.relative_to(root).parts)
    }


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "showcase"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Showcase Test")
    _git(root, "config", "user.email", "showcase-test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.hooksPath", str(root / ".git" / "disabled-hooks"))
    (root / RUN.SENTINEL).write_text("generated showcase\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env\nnode_modules/\n", encoding="utf-8")
    (root / "README.md").write_text("Public baseline.\n", encoding="utf-8")
    (root / "obsolete.md").write_text("Old public document.\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "Create isolated showcase fixture")
    return root


@pytest.fixture
def snapshot(tmp_path: Path, checkout: Path) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / RUN.SENTINEL).write_bytes((checkout / RUN.SENTINEL).read_bytes())
    (root / ".gitignore").write_bytes((checkout / ".gitignore").read_bytes())
    (root / "README.md").write_text("Updated public document.\n", encoding="utf-8")
    (root / "new.md").write_text("New public document.\n", encoding="utf-8")
    return root


def _add_ignored_runtime(checkout: Path) -> None:
    sensitive = RUN.PRIVATE_WIN_HOME + "/local-runtime\n"
    (checkout / ".env").write_text(sensitive, encoding="utf-8")
    dependencies = checkout / "frontend" / "node_modules"
    dependencies.mkdir(parents=True)
    (dependencies / "local.txt").write_text(sensitive, encoding="utf-8")


@pytest.mark.parametrize("dirty_kind", ["unstaged", "staged", "deleted", "untracked"])
def test_sync_rejects_dirty_checkout_without_overwriting_or_removing_files(
    checkout: Path, snapshot: Path, dirty_kind: str
) -> None:
    if dirty_kind == "deleted":
        (checkout / "README.md").unlink()
    elif dirty_kind == "untracked":
        (checkout / "user-notes.md").write_text("Keep my notes.\n", encoding="utf-8")
    else:
        (checkout / "README.md").write_text("Keep my changes.\n", encoding="utf-8")
        if dirty_kind == "staged":
            _git(checkout, "add", "README.md")
    before = _files(checkout)
    staged_before = _git(checkout, "diff", "--cached", "--binary")

    with pytest.raises(SystemExit):
        RUN.sync_snapshot_to_checkout(snapshot, checkout)

    assert _files(checkout) == before
    assert _git(checkout, "diff", "--cached", "--binary") == staged_before


def test_clean_sync_updates_snapshot_and_preserves_ignored_runtime(
    checkout: Path, snapshot: Path
) -> None:
    _add_ignored_runtime(checkout)
    runtime_before = {
        name: content
        for name, content in _files(checkout).items()
        if name == ".env" or "node_modules/" in name
    }
    assert not _git(checkout, "status", "--porcelain")

    RUN.sync_snapshot_to_checkout(snapshot, checkout)

    assert (checkout / "README.md").read_bytes() == (snapshot / "README.md").read_bytes()
    assert (checkout / "new.md").read_bytes() == (snapshot / "new.md").read_bytes()
    assert not (checkout / "obsolete.md").exists()
    for name, content in runtime_before.items():
        assert (checkout / name).read_bytes() == content


def _forbid_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("This operation must not prepare, generate, or write a snapshot report")

    for name in ("prepare_output", "build_snapshot", "write_report"):
        monkeypatch.setattr(RUN, name, unexpected)


def test_verify_checkout_is_read_only_and_ignores_local_runtime(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_ignored_runtime(checkout)
    (checkout / "README.md").write_text("Public uncommitted edit.\n", encoding="utf-8")
    (checkout / "new-public.md").write_text("Public untracked document.\n", encoding="utf-8")
    before = _files(checkout, include_git=True)
    _forbid_generation(monkeypatch)

    assert RUN.main(["--verify-checkout", str(checkout)]) == 0

    assert _files(checkout, include_git=True) == before


@pytest.mark.parametrize("source", ["unstaged", "staged", "untracked"])
def test_verify_checkout_detects_sensitive_current_worktree_content(
    checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    target = checkout / ("private-notes.md" if source == "untracked" else "README.md")
    target.write_text(RUN.PRIVATE_WIN_HOME + "/private-notes\n", encoding="utf-8")
    if source == "staged":
        _git(checkout, "add", target.name)
    before = _files(checkout, include_git=True)
    _forbid_generation(monkeypatch)

    assert RUN.main(["--verify-checkout", str(checkout)]) == 1

    captured = capsys.readouterr()
    assert target.name in captured.out + captured.err
    assert _files(checkout, include_git=True) == before


@pytest.mark.parametrize(
    "other_args",
    [["--output", "unused"], ["--replace"], ["--init-git"], ["--sync-checkout", "unused"]],
)
def test_verify_checkout_rejects_generation_flags(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, other_args: list[str]
) -> None:
    _forbid_generation(monkeypatch)
    before = _files(checkout, include_git=True)

    with pytest.raises(SystemExit) as error:
        RUN.main(["--verify-checkout", str(checkout), *other_args])

    assert error.value.code != 0
    assert _files(checkout, include_git=True) == before


def test_main_rejects_dirty_sync_before_generating_snapshot(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (checkout / "user-notes.md").write_text("Keep these notes.\n", encoding="utf-8")
    before = _files(checkout)
    _forbid_generation(monkeypatch)

    with pytest.raises(SystemExit):
        RUN.main(["--sync-checkout", str(checkout)])

    assert _files(checkout) == before


def test_sync_rechecks_dirty_state_after_snapshot_generation(
    checkout: Path, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def build_with_concurrent_edit(_output: Path) -> list[str]:
        (checkout / "user-notes.md").write_text("Concurrent user edit.\n", encoding="utf-8")
        return ["README.md"]

    monkeypatch.setattr(RUN, "prepare_output", lambda *_args: None)
    monkeypatch.setattr(RUN, "build_snapshot", build_with_concurrent_edit)
    monkeypatch.setattr(RUN, "write_report", lambda *_args: None)

    with pytest.raises(SystemExit):
        RUN.main(["--sync-checkout", str(checkout), "--output", str(snapshot)])

    assert (checkout / "README.md").read_text(encoding="utf-8") == "Public baseline.\n"
    assert (checkout / "obsolete.md").exists()
    assert (checkout / "user-notes.md").read_text(encoding="utf-8") == "Concurrent user edit.\n"


@pytest.mark.parametrize("relation", ["same", "inside", "parent"])
def test_sync_rejects_overlapping_output_before_preparing_it(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, relation: str
) -> None:
    output = {
        "same": checkout,
        "inside": checkout / "nested-output",
        "parent": checkout.parent,
    }[relation]
    before = _files(checkout, include_git=True)
    _forbid_generation(monkeypatch)

    with pytest.raises(SystemExit):
        RUN.main(["--sync-checkout", str(checkout), "--output", str(output), "--replace"])

    assert _files(checkout, include_git=True) == before


@pytest.mark.parametrize("operation", ["verify", "sync"])
def test_checkout_validation_rejects_nested_directory_with_fake_git_marker(
    checkout: Path, snapshot: Path, operation: str
) -> None:
    nested = checkout / "nested"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / RUN.SENTINEL).write_text("fake root\n", encoding="utf-8")
    before = _files(checkout, include_git=True)

    with pytest.raises(SystemExit):
        if operation == "verify":
            RUN.main(["--verify-checkout", str(nested)])
        else:
            RUN.sync_snapshot_to_checkout(snapshot, nested)

    assert _files(checkout, include_git=True) == before


def test_verify_wrapper_does_not_resolve_or_modify_source_project(
    tmp_path: Path, checkout: Path
) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is unavailable for the wrapper isolation regression")
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        wrapper_name = "run.ps1"
        shell_args = ["-NoProfile", "-NonInteractive", "-File"]
    else:
        shell = shutil.which("bash")
        wrapper_name = "run.sh"
        shell_args = []
    if shell is None:
        pytest.skip("The platform wrapper shell is unavailable")

    source = tmp_path / "private-source"
    script_dir = source / "scripts" / "showcase-snapshot"
    script_dir.mkdir(parents=True)
    shutil.copyfile(RUN_PATH, script_dir / "snapshot.py")
    wrapper = script_dir / wrapper_name
    shutil.copyfile(RUN_PATH.with_name(wrapper_name), wrapper)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "isolated-showcase-source"\nversion = "0.0.0"\n'
        'dependencies = ["codex-showcase-test-unresolvable-dependency-987654321==0.0.0"]\n',
        encoding="utf-8",
    )
    source_before = _files(source, include_git=True)
    checkout_before = _files(checkout, include_git=True)
    env = os.environ.copy()
    env.update(
        UV_OFFLINE="1",
        UV_PYTHON=sys.executable,
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )

    result = subprocess.run(
        [shell, *shell_args, str(wrapper), "--verify-checkout", str(checkout)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _files(source, include_git=True) == source_before
    assert _files(checkout, include_git=True) == checkout_before
    assert not (source / ".venv").exists()
    assert not (source / "uv.lock").exists()
