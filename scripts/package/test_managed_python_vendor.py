from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

VENDOR_PATH = Path(__file__).parent / "vendor" / "managed_python.py"


def _load_vendor() -> ModuleType:
    spec = importlib.util.spec_from_file_location("managed_python_vendor_test", VENDOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


managed_python = _load_vendor()


def test_managed_python_lock_is_immutable_windows_x64_contract() -> None:
    lock = managed_python.load_lock()
    assert lock["runtime_version"] == "3.12.14"
    assert lock["python"]["release_tag"] == "20260814"
    assert lock["python"]["sha256"] == (
        "89f18f6932917163b74339ebcec2645c8e47ae7f1c5f2ac37f2b4f4cf3beb647"
    )
    assert lock["uv"]["version"] == "0.12.5"
    assert lock["uv"]["sha256"] == (
        "4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a"
    )
    assert lock["capabilities"]["package_policy"] == "wheel-only-no-build"
    assert lock["capabilities"]["link_mode"] == "copy"
    assert lock["capabilities"]["runtime_downloads"] is False


@pytest.mark.parametrize("name", ["../escape", "/absolute", "nested/../../escape"])
def test_managed_python_archive_path_validation_rejects_escape(tmp_path: Path, name: str) -> None:
    with pytest.raises(RuntimeError, match=r"unsafe|escaped"):
        managed_python._safe_target(tmp_path, name)


def test_managed_python_archive_rejects_duplicate_target(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("uv.exe", b"first")
        bundle.writestr("uv.exe", b"second")
    with pytest.raises(RuntimeError, match="repeats target"):
        managed_python._extract_regular_files(archive, tmp_path / "out")


def test_managed_python_identity_detects_payload_tampering(tmp_path: Path) -> None:
    root = tmp_path / "prepared"
    (root / "base").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "licenses").mkdir()
    (root / "base" / "python.exe").write_bytes(b"python")
    (root / "bin" / "uv.exe").write_bytes(b"uv")
    (root / "licenses" / "LICENSE-CPYTHON.txt").write_text("license", encoding="utf-8")
    lock = managed_python.load_lock()
    (root / "vendor-lock.json").write_text(
        json.dumps(lock, ensure_ascii=False),
        encoding="utf-8",
    )
    managed_python._write_identity(root, lock)
    assert managed_python._verify_prepared(root, lock, run_smoke=False)
    (root / "base" / "python.exe").write_bytes(b"tampered")
    assert not managed_python._verify_prepared(root, lock, run_smoke=False)


def test_managed_python_prepared_payload_excludes_regenerable_bytecode(
    tmp_path: Path,
) -> None:
    root = tmp_path / "base"
    cache = root / "Lib" / "site-packages" / "pip" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "commands.cpython-312.pyc").write_bytes(b"generated")
    source = cache.parent / "commands.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    managed_python._remove_regenerable_bytecode(root)

    assert source.is_file()
    assert not cache.exists()


def test_managed_python_identity_rejects_regenerable_bytecode(tmp_path: Path) -> None:
    root = tmp_path / "prepared"
    (root / "base" / "__pycache__").mkdir(parents=True)
    (root / "base" / "python.exe").write_bytes(b"python")
    (root / "base" / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"generated")
    (root / "bin").mkdir()
    (root / "bin" / "uv.exe").write_bytes(b"uv")
    (root / "licenses").mkdir()
    lock = managed_python.load_lock()
    (root / "vendor-lock.json").write_text(
        json.dumps(lock, ensure_ascii=False),
        encoding="utf-8",
    )
    managed_python._write_identity(root, lock)

    assert not managed_python._verify_prepared(root, lock, run_smoke=False)


def test_managed_python_prepared_payload_excludes_bootstrap_pip(tmp_path: Path) -> None:
    root = tmp_path / "base"
    package = root / "Lib" / "site-packages" / "pip"
    metadata = root / "Lib" / "site-packages" / "pip-1.0.dist-info"
    scripts = root / "Scripts"
    package.mkdir(parents=True)
    metadata.mkdir()
    scripts.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (metadata / "METADATA").write_text("Name: pip\n", encoding="utf-8")
    (scripts / "pip.exe").write_bytes(b"launcher")

    managed_python._remove_bootstrap_pip(root)

    assert not managed_python._contains_bootstrap_pip(root)


def test_managed_python_offline_download_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="offline managed Python"):
        managed_python._download_locked(
            managed_python.load_lock()["python"],
            tmp_path / "python.tar.gz",
            offline=True,
        )
