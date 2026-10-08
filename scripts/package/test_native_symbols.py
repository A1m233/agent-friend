from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import sys
import uuid
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(PACKAGE.parent))
from _lib.native_symbols import BinaryFormatError, Minidump, PeImage, pdb_identity  # noqa: E402
from native_symbols import (  # noqa: E402
    archive_native_symbols,
    validate_archive,
    validate_release_symbols,
)

GUID = uuid.UUID("7d21b5ab-e74f-4802-ac98-12353e030e56")


def sample_binaries(directory: Path, *, age: int = 1) -> Path:
    """Minimal PE+MSF fixtures with independently declared CodeView identity."""
    directory.mkdir(parents=True, exist_ok=True)
    data = bytearray(0x800)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 2, 1234, 0, 0, 240, 0)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<II", data, optional + 56, 0x3000, 0x200)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 112 + 48, 0x2000, 28)
    for i, (name, va, raw, flags) in enumerate(
        ((b".text", 0x1000, 0x200, 0x60000020), (b".rdata", 0x2000, 0x400, 0x40000040))
    ):
        off = optional + 240 + i * 40
        data[off : off + len(name)] = name
        struct.pack_into("<IIII", data, off + 8, 0x200, va, 0x200, raw)
        struct.pack_into("<I", data, off + 36, flags)
    data[0x200:0x204] = b"\x90\x0f\x0b\xc3"
    cv = b"RSDS" + GUID.bytes_le + struct.pack("<I", age) + b"app.pdb\0"
    struct.pack_into("<IIHHIIII", data, 0x400, 0, 1234, 0, 0, 2, len(cv), 0x2040, 0x440)
    data[0x440 : 0x440 + len(cv)] = cv
    image = directory / "agent-friend.exe"
    image.write_bytes(data)
    pdb = bytearray(4 * 512)
    pdb[:32] = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0"
    struct.pack_into("<IIIIII", pdb, 32, 512, 0, 4, 16, 0, 2)
    struct.pack_into("<I", pdb, 1024, 1)
    struct.pack_into("<IIII", pdb, 512, 2, 0, 28, 3)
    struct.pack_into("<III", pdb, 1536, 20000404, 0, age)
    pdb[1548:1564] = GUID.bytes_le
    (directory / "app.pdb").write_bytes(pdb)
    return image


def identity(build_id: str = "test-build") -> dict:
    return {
        "schemaVersion": 1,
        "buildId": build_id,
        "productVersion": "1.2.3",
        "profileId": "production",
        "sourceCommit": "a" * 40,
        "sourceTreeSha256": "b" * 64,
        "dirty": False,
        "buildMode": "formal",
        "distribution": "ordinary",
    }


def attach_symbols(release_root: Path, manifest: dict, source: Path) -> None:
    build = identity(manifest["build_id"])
    build.update(
        productVersion=manifest["version"],
        dirty=manifest["source"]["dirty"],
        buildMode=manifest["build_mode"],
        distribution=manifest["distribution"],
        sourceTreeSha256=manifest["source"]["tree_sha256"],
    )
    manifest["native_symbols"] = archive_native_symbols(
        source, releases_root=release_root, build_identity=build, sensitive=manifest["sensitive"]
    )


def test_pe_and_pdb_bind_by_guid_and_age(tmp_path):
    image = sample_binaries(tmp_path)
    pe = PeImage(image)
    assert pe.codeview["guid"] == str(GUID)
    assert pdb_identity(tmp_path / "app.pdb") == {"guid": str(GUID), "age": 1}
    assert pe.rva_bytes(0x1000, 4) == b"\x90\x0f\x0b\xc3"
    with pytest.raises(BinaryFormatError):
        pe.rva_bytes(0x1200, 1)


def test_symbol_archive_survives_build_cache_removal(tmp_path):
    image = sample_binaries(tmp_path / "cache")
    root = tmp_path / "releases"
    ref = archive_native_symbols(
        image, releases_root=root, build_identity=identity(), sensitive=False
    )
    shutil.rmtree(image.parent)
    directory = (root / ref["path"]).parent
    value = validate_archive(directory)
    assert value["buildIdentity"]["buildId"] == "test-build"
    assert (directory / "app.pdb").exists()
    (directory / "app.pdb").write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        validate_archive(directory)


def test_symbol_archive_rejects_mismatched_pdb_and_overwrite(tmp_path):
    image = sample_binaries(tmp_path / "cache")
    root = tmp_path / "releases"
    ref = archive_native_symbols(
        image, releases_root=root, build_identity=identity(), sensitive=False
    )
    assert (
        archive_native_symbols(
            image, releases_root=root, build_identity=identity(), sensitive=False
        )
        == ref
    )
    data = bytearray(image.read_bytes())
    data[0x200] ^= 1
    image.write_bytes(data)
    with pytest.raises(RuntimeError, match="immutable"):
        archive_native_symbols(
            image, releases_root=root, build_identity=identity(), sensitive=False
        )
    raw = bytearray((image.parent / "app.pdb").read_bytes())
    struct.pack_into("<I", raw, 1544, 2)
    (image.parent / "app.pdb").write_bytes(raw)
    with pytest.raises(RuntimeError, match="GUID-age mismatch"):
        archive_native_symbols(
            image, releases_root=root, build_identity=identity("other"), sensitive=False
        )


def test_resource_overlay_does_not_change_code_binding(tmp_path):
    image = sample_binaries(tmp_path)
    before = PeImage(image).metadata()
    data = bytearray(image.read_bytes())
    data[0x700:0x703] = b"NSS"
    image.write_bytes(data)
    assert PeImage(image).metadata() == before
    data[0x200] = 0xCC
    image.write_bytes(data)
    assert PeImage(image).metadata()["codeSections"] != before["codeSections"]


@pytest.mark.parametrize("malformation", ["pe-rva", "pdb-directory", "pdb-count"])
def test_binary_parsers_reject_out_of_bounds(tmp_path, malformation):
    image = sample_binaries(tmp_path)
    if malformation == "pe-rva":
        data = bytearray(image.read_bytes())
        struct.pack_into("<I", data, 0x98 + 112 + 48, 0xFFFFFFFF)
        image.write_bytes(data)

        def action():
            return PeImage(image)
    else:
        path = tmp_path / "app.pdb"
        data = bytearray(path.read_bytes())
        struct.pack_into("<I", data, 1024 if malformation == "pdb-directory" else 512, 0xFFFFFFFF)
        path.write_bytes(data)

        def action():
            return pdb_identity(path)

    with pytest.raises(BinaryFormatError):
        action()


def test_native_release_requires_correct_archive(tmp_path):
    image = sample_binaries(tmp_path / "cache")
    manifest = {
        "version": "1.2.3",
        "build_id": "test-build",
        "distribution": "ordinary",
        "source": {"commit": "a" * 40, "dirty": False, "tree_sha256": "b" * 64},
        "build_mode": "formal",
        "sensitive": False,
    }
    with pytest.raises(RuntimeError, match="required"):
        validate_release_symbols(tmp_path / "releases", manifest)
    attach_symbols(tmp_path / "releases", manifest, image)
    validate_release_symbols(tmp_path / "releases", manifest)
    manifest["source"]["tree_sha256"] = "c" * 64
    with pytest.raises(RuntimeError, match="different release"):
        validate_release_symbols(tmp_path / "releases", manifest)
    manifest["source"]["tree_sha256"] = "b" * 64
    manifest["source"]["commit"] = "c" * 40
    with pytest.raises(RuntimeError, match="different release"):
        validate_release_symbols(tmp_path / "releases", manifest)
    manifest["native_symbols"]["path"] = "../outside.json"
    with pytest.raises(RuntimeError, match="reference mismatch"):
        validate_release_symbols(tmp_path / "releases", manifest)


def sample_dump(path: Path, *, age: int = 1) -> None:
    data = bytearray(1024)
    data[:4] = b"MDMP"
    struct.pack_into("<II", data, 8, 3, 32)
    for i, (kind, size, off) in enumerate(((4, 112, 80), (7, 56, 200), (6, 168, 256))):
        struct.pack_into("<III", data, 32 + i * 12, kind, size, off)
    struct.pack_into("<I", data, 80, 1)
    struct.pack_into("<QIIII", data, 84, 0x140000000, 0x3000, 0, 1234, 500)
    cv = b"RSDS" + GUID.bytes_le + struct.pack("<I", age) + b"app.pdb\0"
    struct.pack_into("<II", data, 84 + 76, len(cv), 600)
    data[600 : 600 + len(cv)] = cv
    name = r"C:\private\agent-friend.exe".encode("utf-16le")
    struct.pack_into("<I", data, 500, len(name))
    data[504 : 504 + len(name)] = name
    struct.pack_into("<H", data, 200, 9)
    struct.pack_into("<I", data, 256, 42)
    struct.pack_into("<I", data, 264, 0xC000001D)
    struct.pack_into("<Q", data, 280, 0x140001001)
    struct.pack_into("<II", data, 416, 256, 700)
    path.write_bytes(data)


def test_minidump_metadata_and_malformed_stream(tmp_path):
    path = tmp_path / "incident.dmp"
    sample_dump(path)
    dump = Minidump(path)
    assert dump.exception["module"] == "agent-friend.exe"
    assert dump.exception["rva"] == 0x1001
    assert "private" not in json.dumps(dump.metadata())
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 40, 0xFFFFFFFE)
    path.write_bytes(data)
    with pytest.raises(BinaryFormatError):
        Minidump(path)


def test_symbolizer_rejects_other_build_before_native_calls(tmp_path):
    image = sample_binaries(tmp_path / "cache")
    root = tmp_path / "releases"
    ref = archive_native_symbols(
        image, releases_root=root, build_identity=identity(), sensitive=False
    )
    path = tmp_path / "bad.dmp"
    sample_dump(path, age=2)
    spec = importlib.util.spec_from_file_location(
        "crash_diagnostics_test_run", PACKAGE.parent / "crash-diagnostics" / "run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        module.symbolize(path, (root / ref["path"]).parent)


def test_legacy_rust_symbol_display_preserves_unrecognized_names():
    from _lib.native_symbols.stackwalk import display_symbol

    assert display_symbol("_ZN4test5thing17h1234567890abcdefE") == "test::thing"
    assert display_symbol("_ZN4test999thingE") == "_ZN4test999thingE"
    assert display_symbol("_RNewEncoding") == "_RNewEncoding"


@pytest.mark.skipif(os.name != "nt", reason="native StackWalk64 boundary")
def test_stackwalk_rejects_truncated_native_context_before_loading_dbghelp(tmp_path):
    from _lib.native_symbols.stackwalk import walk

    path = tmp_path / "truncated-context.dmp"
    sample_dump(path)
    with pytest.raises(RuntimeError, match="amd64-exception-context-required"):
        walk(Minidump(path), tmp_path / "not-read.exe", tmp_path)


def test_legacy_release_remains_discoverable_with_explicit_symbol_gap(tmp_path):
    from releases import discover_formal_releases, publish_installer
    from test_package import _release_manifest

    root = tmp_path / "releases"
    candidate = tmp_path / "candidate.exe"
    candidate.write_bytes(b"installer")
    manifest = _release_manifest(version="1.2.3", build_id="legacy", local_test=False)
    attach_symbols(root, manifest, sample_binaries(tmp_path / "build"))
    installer = publish_installer(
        candidate, releases_root=root, manifest=manifest, local_test=False
    )
    path = installer.parent / "release.json"
    historical = json.loads(path.read_text(encoding="utf-8"))
    historical.pop("native_symbols")
    path.write_text(json.dumps(historical), encoding="utf-8")
    records, warnings = discover_formal_releases(root)
    assert len(records) == 1
    assert warnings == [f"legacy release has no archived native symbols: {installer.parent}"]
