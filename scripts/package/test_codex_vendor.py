from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "codex_companions_test", Path(__file__).parent / "vendor/codex.py"
)
assert SPEC and SPEC.loader
VENDOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VENDOR)


@pytest.mark.parametrize("damage", ["none", "missing", "modified", "old_identity"])
def test_cached_payload_requires_companion_hashes(tmp_path: Path, damage: str) -> None:
    lock = VENDOR.load_lock()
    platform = VENDOR.platform_key()
    artifact = lock["artifacts"][platform]
    directory = tmp_path / "bin"
    directory.mkdir()
    names = {VENDOR._installed_executable_name()}
    if platform == "windows-x86_64":
        names |= {"codex-windows-sandbox-setup.exe", "codex-command-runner.exe"}
    else:
        names.add("test-companion")
    for name in names:
        (directory / name).write_bytes(name.encode())
    executable = directory / VENDOR._installed_executable_name()
    (tmp_path / "LICENSE-CODEX").write_bytes(b"license")
    lock["license"]["sha256"] = hashlib.sha256(b"license").hexdigest()
    identity = {
        "product": lock["product"],
        "version": lock["version"],
        "platform": platform,
        "archive_sha256": artifact["sha256"],
        "executable_sha256": VENDOR._sha256(executable),
        "protocol": lock["protocol"],
        "bin_sha256": {p.name: VENDOR._sha256(p) for p in directory.iterdir()},
    }
    companion = directory / next(n for n in names if n != executable.name)
    if damage == "missing":
        companion.unlink()
    elif damage == "modified":
        companion.write_bytes(b"changed")
    elif damage == "old_identity":
        identity.pop("bin_sha256")
    (tmp_path / "identity.json").write_text(json.dumps(identity))
    (tmp_path / "vendor-lock.json").write_text(json.dumps(lock))
    assert VENDOR._verify_prepared(tmp_path, executable, lock, artifact) == (damage == "none")
