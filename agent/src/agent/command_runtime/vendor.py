"""Runtime-side verification of the prepared or packaged Codex identity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class CodexIdentityError(RuntimeError):
    pass


def verify_codex_executable(path: Path) -> Path:
    executable = path.resolve()
    root = executable.parent.parent
    identity_path = root / "identity.json"
    lock_path = root / "vendor-lock.json"
    license_path = root / "LICENSE-CODEX"
    if (
        not executable.is_file()
        or not identity_path.is_file()
        or not lock_path.is_file()
        or not license_path.is_file()
    ):
        raise CodexIdentityError("Codex payload is incomplete")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexIdentityError("Codex identity is unreadable") from exc
    if not isinstance(identity, dict) or not isinstance(lock, dict):
        raise CodexIdentityError("Codex identity has an invalid shape")
    protocol = identity.get("protocol")
    platform_key = identity.get("platform")
    artifacts = lock.get("artifacts")
    artifact = artifacts.get(platform_key) if isinstance(artifacts, dict) else None
    if (
        lock.get("schema_version") != 1
        or lock.get("product") != "openai-codex"
        or identity.get("schema_version") != 1
        or identity.get("product") != "openai-codex"
        or identity.get("version") != lock.get("version")
        or identity.get("release_tag") != lock.get("release_tag")
        or not isinstance(artifact, dict)
        or identity.get("archive_sha256") != artifact.get("sha256")
        or protocol != lock.get("protocol")
        or not isinstance(protocol, dict)
        or protocol.get("transport") != "stdio-jsonl"
        or "process/start" not in protocol.get("required_methods", [])
        or "process/closed" not in protocol.get("required_notifications", [])
        or "sandboxType" not in protocol.get("required_start_result", [])
    ):
        raise CodexIdentityError("Codex identity does not satisfy the protocol floor")
    if identity.get("executable_sha256") != _sha256(executable):
        raise CodexIdentityError("Codex executable hash does not match its identity")
    license_spec = lock.get("license")
    if not isinstance(license_spec, dict) or license_spec.get("sha256") != _sha256(license_path):
        raise CodexIdentityError("Codex license hash does not match its lock")
    return executable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
