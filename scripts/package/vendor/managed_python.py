"""下载、校验并 staging 固定的 Windows managed Python payload。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

LOCK_PATH = Path(__file__).with_name("managed_python.lock.json")
IDENTITY_SCHEMA_VERSION = 2


def load_lock() -> dict[str, Any]:
    value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise RuntimeError("unsupported managed Python vendor lock schema")
    if value.get("platform") != "windows" or value.get("architecture") != "x86_64":
        raise RuntimeError("managed Python lock is not the Windows x64 contract")
    return value


def prepare(
    repo_root: Path,
    *,
    cache_base: Path | None = None,
    offline: bool = False,
) -> Path:
    if os.name != "nt":
        raise RuntimeError("managed Python packaged payload currently supports Windows x64 only")
    lock = load_lock()
    destination = (
        cache_base / f"managed-python-{lock['runtime_version']}-windows-x86_64"
        if cache_base is not None
        else repo_root
        / ".cache"
        / "execution-environment"
        / f"managed-python-{lock['runtime_version']}-windows-x86_64"
    )
    if _verify_prepared(destination, lock, run_smoke=False):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    download_root = (
        cache_base / "managed-python-downloads" / str(lock["runtime_version"])
        if cache_base is not None
        else repo_root
        / ".cache"
        / "execution-environment"
        / "downloads"
        / str(lock["runtime_version"])
    )
    download_root.mkdir(parents=True, exist_ok=True)
    python_archive = download_root / str(lock["python"]["filename"])
    uv_archive = download_root / str(lock["uv"]["filename"])
    _download_locked(lock["python"], python_archive, offline=offline)
    _download_locked(lock["uv"], uv_archive, offline=offline)
    with tempfile.TemporaryDirectory(prefix="agent-friend-managed-python-vendor-") as value:
        temp = Path(value)
        extracted_python = temp / "extracted-python"
        extracted_uv = temp / "extracted-uv"
        extracted_python.mkdir()
        extracted_uv.mkdir()
        _extract_regular_files(python_archive, extracted_python)
        _extract_regular_files(uv_archive, extracted_uv)

        python_source = extracted_python / str(lock["python"]["archive_root"])
        uv_source = _find_unique(extracted_uv, str(lock["uv"]["executable"]))
        if not (python_source / "python.exe").is_file():
            raise RuntimeError("managed Python archive does not contain python.exe")

        staged = temp / "prepared"
        shutil.copytree(python_source, staged / "base")
        _remove_bootstrap_pip(staged / "base")
        _remove_regenerable_bytecode(staged / "base")
        (staged / "bin").mkdir(parents=True)
        shutil.copy2(uv_source, staged / "bin" / "uv.exe")
        licenses = staged / "licenses"
        licenses.mkdir()
        shutil.copy2(python_source / "LICENSE.txt", licenses / "LICENSE-CPYTHON.txt")
        for license_entry in lock["licenses"]:
            cached_license = download_root / str(license_entry["name"])
            _download_locked(license_entry, cached_license, offline=offline)
            shutil.copy2(cached_license, licenses / str(license_entry["name"]))
        shutil.copy2(LOCK_PATH, staged / "vendor-lock.json")
        _write_identity(staged, lock)
        if not _verify_prepared(staged, lock, run_smoke=True):
            raise RuntimeError(
                "prepared managed Python payload failed identity or capability smoke"
            )
        if destination.exists():
            shutil.rmtree(destination)
        staged.replace(destination)
    if not _verify_prepared(destination, lock, run_smoke=False):
        raise RuntimeError("published managed Python payload failed identity verification")
    return destination


def stage_for_package(
    repo_root: Path,
    destination: Path,
    *,
    cache_base: Path,
    offline: bool,
) -> Path:
    source = prepare(repo_root, cache_base=cache_base, offline=offline)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination / "base" / "python.exe"


def verify_prepared(root: Path, *, run_smoke: bool = False) -> Path:
    lock = load_lock()
    if not _verify_prepared(root, lock, run_smoke=run_smoke):
        raise RuntimeError("managed Python vendor cache is missing or does not match the lock")
    return root / "base" / "python.exe"


def _write_identity(root: Path, lock: dict[str, Any]) -> None:
    files = _file_inventory(root, exclude={"identity.json"})
    identity_input = {
        "provider": lock["provider"],
        "runtimeVersion": lock["runtime_version"],
        "platform": lock["platform"],
        "architecture": lock["architecture"],
        "pythonArchiveSha256": lock["python"]["sha256"],
        "uvArchiveSha256": lock["uv"]["sha256"],
        "files": files,
    }
    identity = _sha256_bytes(_canonical_json_bytes(identity_input))
    value = {
        "schemaVersion": IDENTITY_SCHEMA_VERSION,
        "provider": lock["provider"],
        "runtimeVersion": lock["runtime_version"],
        "identity": identity,
        "platform": lock["platform"],
        "architecture": lock["architecture"],
        "baseExecutable": "base/python.exe",
        "uvExecutable": "bin/uv.exe",
        "files": files,
    }
    (root / "identity.json").write_bytes(_canonical_json_bytes(value))


def _verify_prepared(root: Path, lock: dict[str, Any], *, run_smoke: bool) -> bool:
    try:
        if any(path.is_dir() for path in (root / "base").rglob("__pycache__")):
            return False
        if _contains_bootstrap_pip(root / "base"):
            return False
        identity = json.loads((root / "identity.json").read_text(encoding="utf-8"))
        staged_lock = json.loads((root / "vendor-lock.json").read_text(encoding="utf-8"))
        if staged_lock != lock or identity.get("schemaVersion") != IDENTITY_SCHEMA_VERSION:
            return False
        expected_fields = {
            "provider": lock["provider"],
            "runtimeVersion": lock["runtime_version"],
            "platform": lock["platform"],
            "architecture": lock["architecture"],
            "baseExecutable": "base/python.exe",
            "uvExecutable": "bin/uv.exe",
        }
        if any(identity.get(key) != expected for key, expected in expected_fields.items()):
            return False
        files = identity.get("files")
        if not isinstance(files, list) or files != _file_inventory(root, exclude={"identity.json"}):
            return False
        identity_input = {
            "provider": lock["provider"],
            "runtimeVersion": lock["runtime_version"],
            "platform": lock["platform"],
            "architecture": lock["architecture"],
            "pythonArchiveSha256": lock["python"]["sha256"],
            "uvArchiveSha256": lock["uv"]["sha256"],
            "files": files,
        }
        if identity.get("identity") != _sha256_bytes(_canonical_json_bytes(identity_input)):
            return False
        if run_smoke:
            _capability_smoke(root, lock)
        return True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def _capability_smoke(root: Path, lock: dict[str, Any]) -> None:
    python = root / "base" / "python.exe"
    uv = root / "bin" / "uv.exe"
    script = (
        "import csv,json,sqlite3,ssl,sys,tarfile,zipfile;"
        "assert '.'.join(map(str,sys.version_info[:3]))==sys.argv[1]"
    )
    _run([str(python), "-I", "-B", "-c", script, str(lock["runtime_version"])])
    completed = _run([str(uv), "--version"])
    if not completed.stdout.strip().startswith(f"uv {lock['uv']['version']}"):
        raise RuntimeError("managed uv version smoke failed")
    with tempfile.TemporaryDirectory(prefix="agent-friend-managed-python-venv-") as value:
        venv = Path(value) / "venv"
        _run(
            [str(python), "-I", "-B", "-m", "venv", "--without-pip", str(venv)],
            timeout=45,
        )
        venv_python = venv / "Scripts" / "python.exe"
        _run(
            [
                str(venv_python),
                "-I",
                "-B",
                "-c",
                "import sys; assert sys.prefix != sys.base_prefix",
            ]
        )


def _run(argv: list[str], *, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        env=_sanitized_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=timeout,
        text=True,
        encoding="utf-8",
    )


def _sanitized_env() -> dict[str, str]:
    allowed = ("COMSPEC", "SYSTEMDRIVE", "SYSTEMROOT", "WINDIR")
    result = {name: os.environ[name] for name in allowed if name in os.environ}
    result.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INPUT": "1",
            "UV_NO_CONFIG": "1",
            "UV_NO_PYTHON_DOWNLOADS": "1",
        }
    )
    return result


def _download_locked(entry: dict[str, Any], destination: Path, *, offline: bool) -> None:
    try:
        if destination.stat().st_size == int(entry["size"]):
            _require_sha256(destination, str(entry["sha256"]))
            return
    except (OSError, RuntimeError):
        pass
    if offline:
        raise RuntimeError(f"offline managed Python vendor payload is unavailable: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.download")
    request = urllib.request.Request(
        str(entry["url"]), headers={"User-Agent": "agent-friend-vendor/1"}
    )
    with (
        urllib.request.urlopen(request, timeout=120) as response,
        temporary.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)
    if temporary.stat().st_size != int(entry["size"]):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch for {destination.name}")
    _require_sha256(temporary, str(entry["sha256"]))
    os.replace(temporary, destination)


def _extract_regular_files(archive: Path, destination: Path) -> None:
    seen: set[Path] = set()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                target = _safe_target(destination, info.filename)
                if target in seen:
                    raise RuntimeError(f"managed Python archive repeats target: {info.filename}")
                seen.add(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return
    with tarfile.open(archive, mode="r:gz") as bundle:
        for member in bundle.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError(
                    f"managed Python archive contains non-regular entry: {member.name}"
                )
            target = _safe_target(destination, member.name)
            if target in seen:
                raise RuntimeError(f"managed Python archive repeats target: {member.name}")
            seen.add(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError(f"managed Python archive member is unreadable: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _safe_target(root: Path, raw_name: str) -> Path:
    name = PurePosixPath(raw_name.replace("\\", "/"))
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise RuntimeError(f"unsafe managed Python archive path: {raw_name}")
    target = root.joinpath(*name.parts).resolve()
    if root.resolve() not in target.parents:
        raise RuntimeError(f"managed Python archive path escaped extraction root: {raw_name}")
    return target


def _remove_regenerable_bytecode(root: Path) -> None:
    """剔除上游归档中的可再生字节码，避免污染只读基座与 NSIS 长路径。"""

    for directory in sorted(root.rglob("__pycache__"), reverse=True):
        if directory.is_dir():
            shutil.rmtree(directory)


def _remove_bootstrap_pip(root: Path) -> None:
    """受管环境统一由 uv 管包，不携带会绕过策略且路径很深的 bootstrap pip。"""

    site_packages = root / "Lib" / "site-packages"
    pip_package = site_packages / "pip"
    if pip_package.is_dir():
        shutil.rmtree(pip_package)
    for metadata in site_packages.glob("pip-*.dist-info"):
        if metadata.is_dir():
            shutil.rmtree(metadata)
    scripts = root / "Scripts"
    if scripts.is_dir():
        for executable in scripts.glob("pip*.exe"):
            executable.unlink()


def _contains_bootstrap_pip(root: Path) -> bool:
    site_packages = root / "Lib" / "site-packages"
    if (site_packages / "pip").exists() or any(site_packages.glob("pip-*.dist-info")):
        return True
    scripts = root / "Scripts"
    return scripts.is_dir() and any(scripts.glob("pip*.exe"))


def _find_unique(root: Path, name: str) -> Path:
    values = [path for path in root.rglob(name) if path.is_file()]
    if len(values) != 1:
        raise RuntimeError(f"managed Python archive expected one {name}, found {len(values)}")
    return values[0]


def _file_inventory(root: Path, *, exclude: set[str]) -> list[dict[str, object]]:
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return files


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


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = ["load_lock", "prepare", "stage_for_package", "verify_prepared"]
