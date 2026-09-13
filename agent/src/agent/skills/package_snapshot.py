"""Bounded package versions owned by an activation, never by the live directory."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES = 2_048
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_BYTES = 128 * 1024 * 1024


class SkillPackageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _files(root: Path) -> list[Path]:
    pending = [root]
    files: list[Path] = []
    directories = 0
    while pending:
        directory = pending.pop()
        directories += 1
        if directories > MAX_FILES:
            raise SkillPackageError("skill_snapshot_too_large", "Too many Skill directories")
        with os.scandir(directory) as entries:
            for entry in entries:
                if directory == root and entry.name == ".git":
                    continue
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    raise SkillPackageError("skill_snapshot_unsafe_file", "Skill contains a link")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    files.append(Path(entry.path))
                else:
                    raise SkillPackageError(
                        "skill_snapshot_unsafe_file", "Skill contains a special file"
                    )
                if len(files) + len(pending) > MAX_FILES:
                    raise SkillPackageError("skill_snapshot_too_large", "Too many Skill files")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix().encode("utf-8"))


def package_digest(root: Path, *, copy_to: Path | None = None) -> str:
    """Use the install transaction's length-delimited digest, with bounded reads."""
    digest = hashlib.sha256()
    total = 0
    files = _files(root)
    for path in files:
        relative = path.relative_to(root)
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or path.is_symlink():
            raise SkillPackageError("skill_snapshot_unsafe_file", "Skill file was replaced")
        with path.open("rb") as reader:
            payload = reader.read(MAX_FILE_BYTES + 1)
        total += len(payload)
        if len(payload) > MAX_FILE_BYTES or total > MAX_BYTES:
            raise SkillPackageError("skill_snapshot_too_large", "Skill package exceeds its budget")
        after = path.stat(follow_symlinks=False)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or len(payload) != before.st_size:
            raise SkillPackageError("skill_changed_during_snapshot", "Skill changed during capture")
        encoded = relative.as_posix().encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        if copy_to is not None:
            target = copy_to / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    if files != _files(root):
        raise SkillPackageError("skill_changed_during_snapshot", "Skill file list changed")
    return digest.hexdigest()


@dataclass(frozen=True)
class SkillPackageSnapshot:
    root: Path
    fingerprint: str
    _owner: tempfile.TemporaryDirectory[str] = field(repr=False, compare=False)

    def close(self) -> None:
        self._owner.cleanup()


def capture_package(root: Path) -> SkillPackageSnapshot:
    owner = tempfile.TemporaryDirectory(prefix="agent-friend-skill-")
    target = Path(owner.name) / root.name
    target.mkdir()
    try:
        fingerprint = package_digest(root, copy_to=target)
        if fingerprint != package_digest(root):
            raise SkillPackageError("skill_changed_during_snapshot", "Skill changed during capture")
        for path in _files(target):
            path.chmod(stat.S_IREAD)
        return SkillPackageSnapshot(target, fingerprint, owner)
    except BaseException:
        owner.cleanup()
        raise
