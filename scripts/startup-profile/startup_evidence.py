"""Local evidence helpers shared by the startup measurement commands."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def source_identity() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, encoding="utf-8", errors="strict"
        ).strip()

    paths = git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        "frontend/src-tauri/src",
        "frontend/src-tauri/Cargo.toml",
        "frontend/src-tauri/Cargo.lock",
        "frontend/src-tauri/build.rs",
        "frontend/src-tauri/tauri.conf.json",
        "scripts/startup-profile",
    ).splitlines()
    hashes = {
        name: digest(ROOT / name) if (ROOT / name).is_file() else None
        for name in sorted(set(paths))
    }
    return {
        "head": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "measurementInputHashes": hashes,
        "scope": "measurement source, not package input identity",
    }


def report_header(kind: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": kind,
        "startedAt": now(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "runnerSource": source_identity(),
    }


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value
