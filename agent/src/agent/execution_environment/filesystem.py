"""Filesystem-only paths for managed environments, including Windows long paths."""

from __future__ import annotations

import ntpath
import os
import shutil
from pathlib import Path


def _windows_filesystem_path(path: str) -> str:
    """Normalize lexically without following directory links or junctions."""
    absolute = ntpath.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def filesystem_path(path: Path) -> Path:
    """Convert only local filesystem I/O paths; keep public paths unchanged."""
    if os.name != "nt":
        return path
    return Path(_windows_filesystem_path(str(path)))


def remove_tree(path: Path) -> None:
    """Delete a managed tree without hiding missing paths or cleanup failures."""
    shutil.rmtree(filesystem_path(path))
