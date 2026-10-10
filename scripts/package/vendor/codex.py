"""Download, verify and stage the pinned official Codex release payload."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

LOCK_PATH = Path(__file__).with_name("codex.lock.json")
RELEASE_DOWNLOAD = "https://github.com/openai/codex/releases/download/{tag}/{filename}"


def load_lock() -> dict[str, Any]:
    value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise RuntimeError("unsupported Codex vendor lock schema")
    return value


def platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    prefix = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(system)
    if prefix is None:
        raise RuntimeError(f"unsupported command runtime platform: {system}-{machine}")
    return f"{prefix}-{architecture}"


def cache_root(repo_root: Path, lock: dict[str, Any] | None = None) -> Path:
    value = lock or load_lock()
    return repo_root / ".cache" / "command-runtime" / "codex" / value["version"] / platform_key()


def prepare(
    repo_root: Path,
    *,
    cache_base: Path | None = None,
    offline: bool = False,
) -> Path:
    lock = load_lock()
    artifact = _artifact(lock)
    destination = (
        cache_base / f"codex-{lock['version']}-{platform_key()}"
        if cache_base is not None
        else cache_root(repo_root, lock)
    )
    executable = destination / "bin" / _installed_executable_name()
    if _verify_prepared(destination, executable, lock, artifact):
        return executable

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="agent-friend-codex-vendor-", dir=destination.parent
    ) as temp_value:
        temp = Path(temp_value)
        archive = temp / artifact["filename"]
        _download(
            RELEASE_DOWNLOAD.format(tag=lock["release_tag"], filename=artifact["filename"]),
            archive,
            offline=offline,
        )
        _require_sha256(archive, artifact["sha256"])
        extracted = temp / "extracted"
        extracted.mkdir()
        _extract_regular_files(archive, extracted)
        source_executable = _find_unique(extracted, artifact["executable"])
        staged = temp / "prepared"
        bin_dir = staged / "bin"
        bin_dir.mkdir(parents=True)
        for source in sorted(extracted.rglob("*")):
            if not source.is_file():
                continue
            target_name = (
                _installed_executable_name() if source == source_executable else source.name
            )
            shutil.copy2(source, bin_dir / target_name)
        if os.name != "nt":
            executable_mode = (bin_dir / _installed_executable_name()).stat().st_mode
            (bin_dir / _installed_executable_name()).chmod(executable_mode | stat.S_IXUSR)
        license_path = staged / "LICENSE-CODEX"
        _download(lock["license"]["url"], license_path, offline=offline)
        _require_sha256(license_path, lock["license"]["sha256"])
        identity = {
            "schema_version": 1,
            "product": lock["product"],
            "version": lock["version"],
            "release_tag": lock["release_tag"],
            "platform": platform_key(),
            "archive_sha256": artifact["sha256"],
            "executable_sha256": _sha256(bin_dir / _installed_executable_name()),
            "bin_sha256": {p.name: _sha256(p) for p in sorted(bin_dir.iterdir()) if p.is_file()},
            "protocol": lock["protocol"],
        }
        (staged / "identity.json").write_text(
            json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copy2(LOCK_PATH, staged / "vendor-lock.json")
        if destination.exists():
            shutil.rmtree(destination)
        staged.replace(destination)
    if not _verify_prepared(destination, executable, lock, artifact):
        raise RuntimeError("prepared Codex payload failed identity verification")
    return executable


def verify_prepared(repo_root: Path) -> Path:
    lock = load_lock()
    artifact = _artifact(lock)
    root = cache_root(repo_root, lock)
    executable = root / "bin" / _installed_executable_name()
    if not _verify_prepared(root, executable, lock, artifact):
        raise RuntimeError("Codex vendor cache is missing or does not match the lock")
    return executable


def stage_prepared(repo_root: Path, destination: Path) -> Path:
    source = verify_prepared(repo_root).parent.parent
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination / "bin" / _installed_executable_name()


def stage_for_package(
    repo_root: Path,
    destination: Path,
    *,
    cache_base: Path,
    offline: bool,
) -> Path:
    executable = prepare(repo_root, cache_base=cache_base, offline=offline)
    source = executable.parent.parent
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination / "bin" / _installed_executable_name()


def _artifact(lock: dict[str, Any]) -> dict[str, str]:
    value = lock["artifacts"].get(platform_key())
    if not isinstance(value, dict):
        raise RuntimeError(f"Codex release is not locked for {platform_key()}")
    return value


def _installed_executable_name() -> str:
    return "codex.exe" if os.name == "nt" else "codex"


def _verify_prepared(
    root: Path,
    executable: Path,
    lock: dict[str, Any],
    artifact: dict[str, str],
) -> bool:
    try:
        identity = json.loads((root / "identity.json").read_text(encoding="utf-8"))
        staged_lock = json.loads((root / "vendor-lock.json").read_text(encoding="utf-8"))
        _require_sha256(root / "LICENSE-CODEX", lock["license"]["sha256"])
        return (
            staged_lock == lock
            and identity["product"] == lock["product"]
            and executable.is_file()
            and identity["version"] == lock["version"]
            and identity["platform"] == platform_key()
            and identity["archive_sha256"] == artifact["sha256"]
            and identity["executable_sha256"] == _sha256(executable)
            and identity["protocol"] == lock["protocol"]
            and _verify_bin_files(root / "bin", identity)
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError, RuntimeError):
        return False


def _verify_bin_files(bin_dir: Path, identity: dict[str, Any]) -> bool:
    hashes = identity.get("bin_sha256")
    required = {_installed_executable_name()}
    if identity.get("platform") == "windows-x86_64":
        required.update({"codex-windows-sandbox-setup.exe", "codex-command-runner.exe"})
    if not isinstance(hashes, dict) or not required.issubset(hashes):
        return False
    for name, digest in hashes.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            return False
        path = bin_dir / name
        if path.is_symlink() or not path.is_file() or _sha256(path) != digest:
            return False
    return True


def _download(url: str, destination: Path, *, offline: bool = False) -> None:
    if offline:
        raise RuntimeError(f"offline Codex vendor payload is unavailable: {destination}")
    request = urllib.request.Request(url, headers={"User-Agent": "agent-friend-vendor/1"})
    with (
        urllib.request.urlopen(request, timeout=120) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


def _extract_regular_files(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                target = _safe_target(destination, info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return
    with tarfile.open(archive, mode="r:gz") as bundle:
        for member in bundle.getmembers():
            if not member.isfile():
                if member.isdir():
                    continue
                raise RuntimeError(f"Codex archive contains non-regular entry: {member.name}")
            target = _safe_target(destination, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError(f"Codex archive member is unreadable: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _safe_target(root: Path, raw_name: str) -> Path:
    name = PurePosixPath(raw_name.replace("\\", "/"))
    if name.is_absolute() or ".." in name.parts:
        raise RuntimeError(f"unsafe Codex archive path: {raw_name}")
    target = root.joinpath(*name.parts).resolve()
    if root.resolve() not in target.parents:
        raise RuntimeError(f"Codex archive path escaped extraction root: {raw_name}")
    return target


def _find_unique(root: Path, name: str) -> Path:
    values = [path for path in root.rglob(name) if path.is_file()]
    if len(values) != 1:
        raise RuntimeError(f"Codex archive expected exactly one {name}, found {len(values)}")
    return values[0]


def _require_sha256(path: Path, expected: str) -> None:
    observed = _sha256(path)
    if observed != expected:
        raise RuntimeError(f"SHA-256 mismatch for {path.name}: expected {expected}, got {observed}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
