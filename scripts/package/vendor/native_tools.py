"""Stage immutable, checksummed native executables and their complete distributions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .managed_python import _download_locked, _extract_regular_files, _file_inventory

LOCK_PATH = Path(__file__).with_name("native_tools.lock.json")


def prepare(*, cache_base: Path, offline: bool = False) -> Path:
    if os.name != "nt":
        raise RuntimeError("Native tools payload currently supports Windows x64 only")
    lock_bytes = LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    identity = hashlib.sha256(lock_bytes).hexdigest()
    destination = cache_base / f"native-tools-{identity}"
    if destination.is_dir():
        verify(destination)
        return destination
    cache_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-tools-", dir=cache_base) as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        tools: dict[str, str] = {}
        for entry in lock["archives"]:
            archive = cache_base / "native-downloads" / entry["filename"]
            _download_locked(entry, archive, offline=offline)
            target = staged / entry["name"]
            _extract_regular_files(archive, target)
            for alias, filename in entry["executables"].items():
                candidates = list(target.rglob(filename))
                if len(candidates) != 1:
                    raise RuntimeError(f"Expected one {filename} in native payload")
                tools[alias] = candidates[0].relative_to(staged).as_posix()
        (staged / "vendor-lock.json").write_bytes(lock_bytes)
        manifest = {
            "schemaVersion": 1,
            "identity": identity,
            "tools": tools,
            "files": _file_inventory(staged, exclude={"identity.json"}),
        }
        (staged / "identity.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        verify(staged)
        staged.replace(destination)
    return destination


def verify(root: Path) -> None:
    from agent.execution_environment.native import NativeTools

    NativeTools(root).validate()
    if (root / "vendor-lock.json").read_bytes() != LOCK_PATH.read_bytes():
        raise RuntimeError("Native tools vendor lock mismatch")


def stage_for_package(destination: Path, *, cache_base: Path, offline: bool) -> None:
    source = prepare(cache_base=cache_base, offline=offline)
    if destination.exists():
        verify(destination)
        return
    shutil.copytree(source, destination)
    verify(destination)
