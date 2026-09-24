"""Session-scoped managed command workspace paths."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

MAX_SESSION_WORKSPACE_FILES = 2_000
MAX_SESSION_WORKSPACE_FILE_BYTES = 100 * 1024 * 1024
MAX_SESSION_WORKSPACE_BYTES = 1024 * 1024 * 1024


def remove_readonly_file(function: Callable[..., object], path: str, exc: BaseException) -> None:
    """rmtree onexc callback: only retry an unlinked, read-only regular file."""
    candidate = Path(path)
    reject_linked_path(candidate)
    info = candidate.stat()
    if not isinstance(exc, PermissionError) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise exc
    candidate.chmod(stat.S_IREAD | stat.S_IWRITE)
    function(path)


def reject_linked_path(path: Path) -> None:
    """Reject links/reparse points in an existing path's ancestry, without following them."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("session_workspace_unsafe_path")


def check_workspace_budget(
    root: Path,
    *,
    added_bytes: int = 0,
    added_files: int = 0,
    replacing: Path | None = None,
    max_file_bytes: int = MAX_SESSION_WORKSPACE_FILE_BYTES,
    max_bytes: int = MAX_SESSION_WORKSPACE_BYTES,
    max_files: int = MAX_SESSION_WORKSPACE_FILES,
) -> None:
    """Preflight physical workspace usage; this is not an OS-enforced running quota."""
    total = added_bytes
    count = added_files
    if added_bytes > max_file_bytes:
        raise ValueError("session_workspace_quota_exceeded")

    def fail_unreadable(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_unreadable):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            reject_linked_path(path)
        for name in files:
            path = Path(directory) / name
            if path == replacing:
                continue
            size = path.stat().st_size
            if size > max_file_bytes:
                raise ValueError("session_workspace_quota_exceeded")
            count += 1
            total += size
    if total > max_bytes or count > max_files:
        raise ValueError("session_workspace_quota_exceeded")


def session_workspace_path(root: Path, session_id: str) -> Path:
    """Map an opaque logical session id to one stable, portable child directory."""

    if not session_id:
        raise ValueError("session_id 不能为空")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return root.resolve() / f"session-{digest}"
