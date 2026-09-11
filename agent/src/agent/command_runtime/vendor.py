"""Runtime-side verification of the prepared or packaged Codex identity."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
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
    hashes = identity.get("bin_sha256")
    required = {executable.name}
    if platform_key == "windows-x86_64":
        required.update({"codex-windows-sandbox-setup.exe", "codex-command-runner.exe"})
    if not isinstance(hashes, dict) or not required.issubset(hashes):
        raise CodexIdentityError(
            "Codex companion identity is incomplete; rebuild the pinned payload"
        )
    for name, digest in hashes.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise CodexIdentityError("Codex companion identity path is invalid")
        companion = executable.parent / name
        if companion.is_symlink() or not companion.is_file() or _sha256(companion) != digest:
            raise CodexIdentityError("Codex companion is missing or its hash does not match")
    license_spec = lock.get("license")
    if not isinstance(license_spec, dict) or license_spec.get("sha256") != _sha256(license_path):
        raise CodexIdentityError("Codex license hash does not match its lock")
    return executable


def probe_setup_helper(executable: Path) -> dict[str, object]:
    """Reach argument parsing only: never pass a payload or request elevation."""
    if os.name != "nt":
        return {"status": "not_applicable"}
    helper = executable.parent / "codex-windows-sandbox-setup.exe"
    env = {
        k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    try:
        completed = subprocess.run(
            [str(helper)],
            env=env,
            cwd=helper.parent,
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timed_out"}
    except OSError as exc:
        return {
            "status": "launch_failed",
            "nativeCode": exc.winerror if hasattr(exc, "winerror") else exc.errno,
        }
    expected = b"helper_request_args_failed: expected payload argument"
    return {
        "status": "ready"
        if completed.returncode == 1 and expected in completed.stderr
        else "failed",
        "exitCode": completed.returncode,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
