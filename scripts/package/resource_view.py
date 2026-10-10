"""Temporary Windows resource view for Tauri's drive-prefix normalization."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.files import no_links, volume


@contextmanager
def junction_view(target: Path, parent: Path) -> Iterator[Path]:
    """Remove only our checked junction, never recursively traverse its target."""
    target = no_links(target)
    parent = no_links(parent)
    parent.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix="tauri-", dir=parent))
    link = holder / "build"
    try:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise RuntimeError("PowerShell is required for the Windows cross-volume resource view")
        subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                "-Path $env:AGENT_FRIEND_TAURI_LINK -Target $env:AGENT_FRIEND_TAURI_SOURCE | Out-Null",
            ],
            env={
                **os.environ,
                "AGENT_FRIEND_TAURI_LINK": str(link),
                "AGENT_FRIEND_TAURI_SOURCE": str(target),
            },
            check=True,
            capture_output=True,
        )
        if not link.is_junction() or link.resolve() != target:
            raise RuntimeError("Tauri resource view did not resolve to the owned build")
        yield link
    finally:
        if os.path.lexists(link):
            if not link.is_junction() or link.resolve() != target:
                raise RuntimeError(f"Tauri resource view changed; refusing cleanup: {link}")
            link.rmdir()
        holder.rmdir()


@contextmanager
def resource_view(paths: Any) -> Iterator[Path]:
    if os.name != "nt" or volume(paths.build_dir)[0] == volume(paths.roots.repo_root)[0]:
        yield paths.build_dir
        return
    # Tiny view metadata only; the payload stays in its registered build object.
    parent = Storage(paths.roots.repo_root).control / "tauri-resource-views"
    if volume(parent)[0] != volume(paths.roots.repo_root)[0]:
        parent = paths.roots.repo_root.parent
    if parent == paths.roots.repo_root or parent.is_relative_to(paths.roots.repo_root):
        raise RuntimeError("Tauri resource view must be outside the source checkout")
    with junction_view(paths.build_dir, parent) as link:
        yield link
