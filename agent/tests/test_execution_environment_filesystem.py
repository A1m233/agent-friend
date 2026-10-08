from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent.execution_environment import filesystem


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"C:\managed\venv", r"\\?\C:\managed\venv"),
        ("C:/managed/old/../venv", r"\\?\C:\managed\venv"),
        (r"\\server\share\managed\venv", r"\\?\UNC\server\share\managed\venv"),
        (r"\\?\C:\managed\venv", r"\\?\C:\managed\venv"),
        (r"\\?\UNC\server\share\venv", r"\\?\UNC\server\share\venv"),
    ],
)
def test_windows_filesystem_paths_are_absolute_and_idempotent(source: str, expected: str) -> None:
    converted = filesystem._windows_filesystem_path(source)
    assert converted == expected
    assert filesystem._windows_filesystem_path(converted) == expected


def test_non_windows_filesystem_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path("relative") / "environment"
    monkeypatch.setattr(filesystem, "os", SimpleNamespace(name="posix"))
    assert filesystem.filesystem_path(path) is path


@pytest.mark.skipif(os.name != "nt", reason="Windows path normalization")
def test_windows_relative_filesystem_path_uses_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("environment")
    converted = filesystem.filesystem_path(path)
    assert converted.is_absolute()
    assert str(converted).startswith("\\\\?\\")
    assert converted == filesystem.filesystem_path(tmp_path / path)


def _assert_owned_path(path: Path, temporary_root: Path) -> None:
    absolute = path.absolute()
    owner = temporary_root.absolute()
    assert absolute != owner
    assert absolute.is_relative_to(owner)


def test_remove_tree_preserves_missing_path_failure(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    _assert_owned_path(missing, tmp_path)
    with pytest.raises(FileNotFoundError):
        filesystem.remove_tree(missing)


def test_remove_tree_preserves_permission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "environment"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("preserved", encoding="utf-8")
    failure = PermissionError("fixture cleanup denied")

    def deny_delete(path: Path) -> None:
        assert path == filesystem.filesystem_path(root)
        raise failure

    monkeypatch.setattr(shutil, "rmtree", deny_delete)
    _assert_owned_path(root, tmp_path)
    with pytest.raises(PermissionError) as error:
        filesystem.remove_tree(root)
    assert error.value is failure
    assert sentinel.read_text(encoding="utf-8") == "preserved"


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path filesystem boundary")
def test_remove_tree_cleans_real_windows_long_paths(tmp_path: Path) -> None:
    root = tmp_path / "long-environment"
    absolute = str(root.absolute())
    extended = (
        "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
    )
    io_root = Path(extended)
    relative = Path("a" * 80) / ("b" * 80) / ("c" * 80) / "payload.txt"
    assert len(str(root / relative)) > 260
    _assert_owned_path(root, tmp_path)
    try:
        (io_root / relative).parent.mkdir(parents=True)
        (io_root / relative).write_bytes(b"long-path fixture")
        filesystem.remove_tree(root)
        assert not io_root.exists()
    finally:
        _assert_owned_path(root, tmp_path)
        if io_root.exists():
            shutil.rmtree(io_root)


def test_remove_tree_does_not_follow_directory_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("preserved", encoding="utf-8")
    link = tmp_path / "directory-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    _assert_owned_path(link, tmp_path)
    try:
        with pytest.raises(OSError):
            filesystem.remove_tree(link)
        assert sentinel.read_text(encoding="utf-8") == "preserved"
    finally:
        _assert_owned_path(link, tmp_path)
        if link.is_symlink():
            link.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_remove_tree_does_not_follow_directory_junction(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("preserved", encoding="utf-8")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=True,
    )
    _assert_owned_path(junction, tmp_path)
    try:
        with pytest.raises(OSError, match="symbolic link"):
            filesystem.remove_tree(junction)
        assert sentinel.read_text(encoding="utf-8") == "preserved"
    finally:
        _assert_owned_path(junction, tmp_path)
        if junction.is_junction():
            junction.rmdir()
