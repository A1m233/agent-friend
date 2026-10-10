"""Conservative path validation and small atomic records."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

GIB = 1024**3
MARKER = ".agent-friend-build-object.json"


def identity(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()[:24]


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError(f"unsupported or invalid build-storage record: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def no_links(path: Path) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    for part in (absolute, *absolute.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"build path traverses a link: {part}")
    return absolute.resolve()


def safe_directory(path: Path, repo: Path, control: Path) -> Path:
    target = no_links(path)
    forbidden = (Path(target.anchor), Path.home().resolve(), repo.resolve(), control.resolve())
    if any(target == p or p.is_relative_to(target) for p in forbidden):
        raise ValueError(f"refusing broad build directory: {target}")
    if target.is_relative_to(repo.resolve()) or target.is_relative_to(control.resolve()):
        raise ValueError(f"build directory must be outside source and control: {target}")
    # Any other checkout is a source tree too; do not adopt its ignored target.
    if any((p / ".git").exists() for p in (target, *target.parents)):
        raise ValueError(f"build directory is inside a Git checkout: {target}")
    return target


def nearest_existing(path: Path) -> Path:
    while not path.exists():
        if path.parent == path:
            raise ValueError(f"cannot identify volume for {path}")
        path = path.parent
    return path


def volume(path: Path) -> tuple[str, int]:
    ancestor = nearest_existing(path.resolve())
    return str(ancestor.stat().st_dev), shutil.disk_usage(ancestor).free


def tree_size(path: Path) -> int:
    """Logical bytes; reject links and read errors rather than undercounting silently."""
    no_links(path)
    total = 0
    stack = [path]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                if entry.is_symlink() or entry.is_junction():
                    raise ValueError(f"linked content in build object: {entry.path}")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.name != MARKER:
                    total += entry.stat(follow_symlinks=False).st_size
    return total
