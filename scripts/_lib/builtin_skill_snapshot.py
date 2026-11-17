"""Publish one packaged builtin Skill and its provenance manifest together."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import portalocker
from agent.skills.first_party_updates.store import package_snapshot_digest


def replace_builtin_snapshot(root: Path, name: str, source: Path, entry: dict[str, Any]) -> None:
    lock_id = hashlib.sha256(os.fsencode(os.path.normcase(root.resolve()))).hexdigest()
    lock = Path(tempfile.gettempdir()) / f"af-builtin-skills-{lock_id}.lock"
    with portalocker.Lock(str(lock), timeout=30):
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 2 or not isinstance(manifest.get("skills"), dict):
            raise ValueError("Unsupported builtin Skill manifest")
        manifest["skills"][name] = entry
        _publish(root, name, source, manifest)


def _publish(root: Path, name: str, source: Path, manifest_value: dict[str, Any]) -> None:
    target = root / name
    staging = root.parent / f".{name}.staging-{uuid4().hex}"
    backup = root.parent / f".{name}.backup-{uuid4().hex}"
    manifest = root / "manifest.json"
    manifest_staging = root.parent / f".manifest.staging-{uuid4().hex}.json"
    if target.resolve() != root.resolve() / name:
        raise ValueError(f"Expected a physical builtin Skill directory: {target}")
    moved_old = False
    installed_new = False
    try:
        shutil.copytree(source, staging, symlinks=True)
        if package_snapshot_digest(staging) != manifest_value["skills"][name]["snapshot_sha256"]:
            raise ValueError(f"Skill contents changed while syncing {name}; retry")
        manifest_staging.write_text(
            json.dumps(manifest_value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
        installed_new = True
        os.replace(manifest_staging, manifest)
    except Exception:
        if installed_new and target.exists():
            shutil.rmtree(target)
        if moved_old and backup.exists():
            os.replace(backup, target)
        raise
    else:
        if moved_old:
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if manifest_staging.exists():
            manifest_staging.unlink()
