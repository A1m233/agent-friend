"""Publish private symbol evidence before publishing a distributable installer."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.native_symbols import PeImage, pdb_identity, sha256
from paths import native_symbols_dir


def _safe_child(directory: Path, name: str) -> Path:
    if not name or Path(name).name != name or any(c in name for c in "\\/:") or name in {".", ".."}:
        raise RuntimeError("invalid symbol artifact name")
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise RuntimeError("symbol artifact escaped archive")
    return path


def validate_archive(directory: Path) -> dict[str, Any]:
    path = _safe_child(directory, "symbols.json")
    if path.stat().st_size > 1024 * 1024:
        raise RuntimeError("symbol manifest too large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1 or manifest.get("platform") != "windows-x64":
        raise RuntimeError("unsupported native symbol manifest")
    modules = manifest.get("modules")
    if not isinstance(modules, list) or len(modules) != 1:
        raise RuntimeError("one desktop-shell symbol module required")
    module = modules[0]
    if module.get("role") != "desktop-shell":
        raise RuntimeError("native symbol role mismatch")
    for key in ("image", "pdb"):
        entry = module[key]
        artifact = _safe_child(directory, entry["name"])
        if artifact.stat().st_size != entry["size"] or sha256(artifact) != entry["sha256"]:
            raise RuntimeError(f"native symbol {key} integrity mismatch")
    image = PeImage(_safe_child(directory, module["image"]["name"]))
    if image.metadata() != module["pe"]:
        raise RuntimeError("native symbol PE metadata mismatch")
    identity = pdb_identity(_safe_child(directory, module["pdb"]["name"]))
    if any(identity[k] != image.codeview[k] for k in ("guid", "age")):
        raise RuntimeError("PE/PDB GUID-age mismatch")
    if module["pdb"]["name"] != image.codeview["pdbName"]:
        raise RuntimeError("PDB basename mismatch")
    return manifest


def archive_native_symbols(
    image_path: Path,
    *,
    releases_root: Path,
    build_identity: dict[str, Any],
    sensitive: bool,
) -> dict[str, Any]:
    image = PeImage(image_path)
    if image.machine != 0x8664 or not image.metadata()["codeSections"]:
        raise RuntimeError("native symbols require executable AMD64 image")
    pdb_path = _safe_child(image_path.parent, image.codeview["pdbName"])
    binding = pdb_identity(pdb_path)
    if any(binding[k] != image.codeview[k] for k in ("guid", "age")):
        raise RuntimeError("PE/PDB GUID-age mismatch")
    final = native_symbols_dir(
        releases_root,
        profile=build_identity["profileId"],
        build_id=build_identity["buildId"],
    )
    if final.exists():
        existing = validate_archive(final)
        if (
            existing["buildIdentity"] != build_identity
            or existing["modules"][0]["image"]["sha256"] != sha256(image_path)
            or existing["modules"][0]["pdb"]["sha256"] != sha256(pdb_path)
            or existing["sensitive"] != sensitive
        ):
            raise RuntimeError("native symbol archive is immutable")
    else:
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".symbols-", dir=final.parent))
        try:
            shutil.copyfile(image_path, staging / image_path.name)
            shutil.copyfile(pdb_path, staging / pdb_path.name)
            manifest = {
                "schemaVersion": 1,
                "platform": "windows-x64",
                "private": True,
                "sensitive": sensitive,
                "buildIdentity": build_identity,
                "modules": [
                    {
                        "role": "desktop-shell",
                        "pe": image.metadata(),
                        "image": {
                            "name": image_path.name,
                            "size": image_path.stat().st_size,
                            "sha256": sha256(image_path),
                        },
                        "pdb": {
                            "name": pdb_path.name,
                            "size": pdb_path.stat().st_size,
                            "sha256": sha256(pdb_path),
                        },
                    }
                ],
            }
            (staging / "symbols.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            (staging / "PRIVATE-SYMBOLS.txt").write_text(
                "Private developer evidence. Do not publish or include in diagnostic bundles.\n"
                + ("SENSITIVE: includes the trusted-beta executable.\n" if sensitive else ""),
                encoding="utf-8",
            )
            validate_archive(staging)
            staging.rename(final)
        finally:
            if staging.exists():
                # Only this operation's checked, newly created staging directory is removed.
                if (
                    staging.resolve().parent != final.parent.resolve()
                    or not staging.name.startswith(".symbols-")
                ):
                    raise RuntimeError("unsafe symbol staging cleanup")
                shutil.rmtree(staging)
    return {
        "path": (final / "symbols.json").relative_to(releases_root).as_posix(),
        "sha256": sha256(final / "symbols.json"),
    }


def validate_release_symbols(releases_root: Path, release: dict[str, Any]) -> None:
    reference = release.get("native_symbols")
    if not isinstance(reference, dict):
        raise RuntimeError("native symbols are required before publishing")
    identity_profile = release.get("profile_id", "production")
    directory = native_symbols_dir(
        releases_root, profile=identity_profile, build_id=release["build_id"]
    )
    expected = (directory / "symbols.json").relative_to(releases_root).as_posix()
    if reference.get("path") != expected or sha256(directory / "symbols.json") != reference.get(
        "sha256"
    ):
        raise RuntimeError("native symbol release reference mismatch")
    manifest = validate_archive(directory)
    identity = manifest["buildIdentity"]
    expected_identity = {
        "buildId": release["build_id"],
        "profileId": identity_profile,
        "productVersion": release["version"],
        "sourceCommit": release["source"]["commit"],
        "sourceTreeSha256": release["source"]["tree_sha256"],
        "dirty": release["source"]["dirty"],
        "distribution": release["distribution"],
        "buildMode": release["build_mode"],
    }
    if (
        any(identity.get(k) != v for k, v in expected_identity.items())
        or manifest["sensitive"] != release["sensitive"]
    ):
        raise RuntimeError("native symbols belong to a different release")
