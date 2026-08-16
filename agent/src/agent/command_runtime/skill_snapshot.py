"""Immutable, bounded execution snapshots for activated Skill packages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..skills.models import ActivatedSkill
from ..tool_runtime.context import ToolRunContext
from .runtime import CommandRuntimeError

MAX_SKILL_SNAPSHOT_FILES = 2_048
MAX_SKILL_SNAPSHOT_FILE_BYTES = 16 * 1024 * 1024
MAX_SKILL_SNAPSHOT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class SkillExecutionSnapshot:
    skill_id: str
    source_root: Path
    snapshot_root: Path
    entry: str
    entry_path: Path
    fingerprint: str
    file_count: int
    total_bytes: int


def prepare_skill_snapshot(
    context: ToolRunContext,
    activation: ActivatedSkill,
    *,
    entry: str,
    execution_root: Path,
) -> SkillExecutionSnapshot:
    """Copy one activated package exactly once for the current run."""

    relative_entry = _relative_entry(entry)
    cache_key = f"skill-snapshot:{activation.record.id}"
    cached = context.command_resource(cache_key)
    if isinstance(cached, SkillExecutionSnapshot):
        if cached.entry != relative_entry.as_posix():
            candidate = _inside(cached.snapshot_root, relative_entry)
            if not candidate.is_file():
                raise CommandRuntimeError(
                    "skill_entry_invalid", "Skill entry is not a regular file in the snapshot"
                )
            return SkillExecutionSnapshot(
                **{**cached.__dict__, "entry": relative_entry.as_posix(), "entry_path": candidate}
            )
        return cached

    source_root = activation.record.package_root.resolve(strict=True)
    execution_root.mkdir(parents=True, exist_ok=True)
    staging = execution_root / f".staging-{uuid4().hex}"
    target = execution_root / f"skill-{uuid4().hex}"
    staging.mkdir()
    manifest: list[dict[str, object]] = []
    total_bytes = 0
    try:
        for source in _walk_regular_files(source_root):
            if len(manifest) >= MAX_SKILL_SNAPSHOT_FILES:
                raise CommandRuntimeError(
                    "skill_snapshot_too_large", "Skill package contains too many files"
                )
            relative = source.relative_to(source_root)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            before = source.stat(follow_symlinks=False)
            if before.st_size > MAX_SKILL_SNAPSHOT_FILE_BYTES:
                raise CommandRuntimeError(
                    "skill_snapshot_too_large", f"Skill file is too large: {relative.as_posix()}"
                )
            digest = hashlib.sha256()
            copied = 0
            with source.open("rb") as reader, destination.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    copied += len(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > MAX_SKILL_SNAPSHOT_BYTES:
                        raise CommandRuntimeError(
                            "skill_snapshot_too_large", "Skill package exceeds snapshot budget"
                        )
                    digest.update(chunk)
                    writer.write(chunk)
            after = source.stat(follow_symlinks=False)
            if (
                copied != before.st_size
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or getattr(before, "st_ino", 0) != getattr(after, "st_ino", 0)
            ):
                raise CommandRuntimeError(
                    "skill_changed_during_snapshot",
                    f"Skill file changed while being copied: {relative.as_posix()}",
                )
            destination.chmod(stat.S_IREAD)
            manifest.append(
                {
                    "path": relative.as_posix(),
                    "size": copied,
                    "sha256": digest.hexdigest(),
                }
            )
        entry_path = _inside(staging, relative_entry)
        if not entry_path.is_file():
            raise CommandRuntimeError(
                "skill_entry_invalid", "Skill entry is not a regular file in the package"
            )
        manifest.sort(key=lambda item: str(item["path"]))
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        staging.rename(target)
        snapshot = SkillExecutionSnapshot(
            skill_id=activation.record.id,
            source_root=source_root,
            snapshot_root=target,
            entry=relative_entry.as_posix(),
            entry_path=target / relative_entry,
            fingerprint=fingerprint,
            file_count=len(manifest),
            total_bytes=total_bytes,
        )
        registered = context.register_command_resource(
            cache_key,
            snapshot,
            lambda: _remove_tree(target),
        )
        assert isinstance(registered, SkillExecutionSnapshot)
        return registered
    except Exception:
        _remove_tree(staging)
        _remove_tree(target)
        raise


def _walk_regular_files(root: Path) -> Iterator[Path]:
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                is_junction = getattr(os.path, "isjunction", lambda _: False)(path)
                if entry.is_symlink() or is_junction:
                    raise CommandRuntimeError(
                        "skill_snapshot_unsafe_file", f"Skill package contains a link: {path.name}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path
                else:
                    raise CommandRuntimeError(
                        "skill_snapshot_unsafe_file",
                        f"Skill package contains a special file: {path.name}",
                    )


def _relative_entry(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        raise CommandRuntimeError("skill_entry_invalid", "Skill entry must use a relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CommandRuntimeError("skill_entry_invalid", "Skill entry escapes the package root")
    return path


def _inside(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CommandRuntimeError(
            "skill_entry_invalid", "Skill entry escapes the package root"
        ) from exc
    return candidate


def _remove_tree(path: Path) -> None:
    def make_writable_and_retry(
        function: Callable[[str], object], value: str, _error: object
    ) -> None:
        Path(value).chmod(stat.S_IWRITE)
        function(value)

    if path.exists():
        shutil.rmtree(path, onerror=make_writable_and_retry)
