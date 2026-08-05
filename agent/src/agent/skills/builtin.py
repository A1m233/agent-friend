"""Materialize packaged builtin Skills into immutable user-data directories."""

from __future__ import annotations

import hashlib
import os
import shutil
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from uuid import uuid4

import portalocker

from ..paths import skills_dir

BUILTIN_PACKAGE = "agent.skills.builtin_assets"


def _resource_files(root: Traversable, prefix: str = "") -> list[tuple[str, Traversable]]:
    files: list[tuple[str, Traversable]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        relative = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_dir():
            if child.name != "__pycache__":
                files.extend(_resource_files(child, relative))
        elif not child.name.endswith(".pyc"):
            files.append((relative, child))
    return files


class BuiltinSkillInstaller:
    """Install the exact packaged bundle once without mutating older versions."""

    def __init__(self, *, target_root: Path | None = None) -> None:
        self._target_root = target_root or skills_dir()

    def install(self) -> Path:
        bundle = resources.files(BUILTIN_PACKAGE)
        packaged_files = _resource_files(bundle)
        files = [
            (
                relative if relative == "manifest.json" else f"skill-creator/{relative}",
                resource,
            )
            for relative, resource in packaged_files
            if relative != "__init__.py"
        ]
        digest = hashlib.sha256()
        for relative, resource in files:
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(resource.read_bytes())
            digest.update(b"\0")
        fingerprint = digest.hexdigest()
        system_root = self._target_root / ".system"
        target = system_root / fingerprint
        self._target_root.mkdir(parents=True, exist_ok=True)
        system_root.mkdir(parents=True, exist_ok=True)

        lock_path = self._target_root / ".system-install.lock"
        with portalocker.Lock(str(lock_path), timeout=30):
            if target.is_dir():
                if not self._matches(target, files):
                    raise RuntimeError(f"builtin Skill bundle verification failed: {target}")
                return target
            staging = system_root / f".{fingerprint}.staging-{os.getpid()}-{uuid4().hex}"
            staging.mkdir(parents=False, exist_ok=False)
            try:
                for relative, resource in files:
                    destination = staging / Path(relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(resource.read_bytes())
                os.replace(staging, target)
            except FileExistsError:
                if not target.is_dir():
                    raise
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return target

    @staticmethod
    def _matches(target: Path, files: list[tuple[str, Traversable]]) -> bool:
        try:
            return all(
                (target / Path(relative)).read_bytes() == resource.read_bytes()
                for relative, resource in files
            )
        except OSError:
            return False
