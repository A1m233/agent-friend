from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import zipfile
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


def test_prepare_publishes_on_destination_volume(tmp_path, monkeypatch):
    lock = VENDOR.load_lock()
    artifact = lock["artifacts"][VENDOR.platform_key()]
    artifact["filename"] = "fixture.zip"
    archive = tmp_path / "fixture.zip"
    names = {artifact["executable"]}
    if VENDOR.platform_key() == "windows-x86_64":
        names |= {"codex-windows-sandbox-setup.exe", "codex-command-runner.exe"}
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in names:
            bundle.writestr(name, name.encode())
    artifact["sha256"] = VENDOR._sha256(archive)
    lock["license"]["sha256"] = hashlib.sha256(b"license").hexdigest()
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(VENDOR, "LOCK_PATH", lock_path)

    def download(url, destination, **kwargs):
        if url.endswith("fixture.zip"):
            shutil.copyfile(archive, destination)
        else:
            destination.write_bytes(b"license")

    cache = tmp_path / "separate-volume"
    original = Path.replace

    def same_volume_replace(source, destination):
        if not source.is_relative_to(cache) or not destination.is_relative_to(cache):
            raise OSError("cross-volume rename")
        return original(source, destination)

    monkeypatch.setattr(VENDOR, "_download", download)
    monkeypatch.setattr(Path, "replace", same_volume_replace)
    executable = VENDOR.prepare(tmp_path / "repo", cache_base=cache)
    assert executable.is_file()
    assert VENDOR.prepare(tmp_path / "repo", cache_base=cache, offline=True) == executable
