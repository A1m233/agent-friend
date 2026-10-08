"""Validate, discover, and publish immutable package artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from native_symbols import validate_release_symbols
from versioning import SemVer

FORMAL_DISTRIBUTIONS = {"ordinary", "trusted-beta"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleaseRecord:
    version: SemVer
    distribution: str
    directory: Path
    manifest: dict[str, Any]


def formal_release_dir(releases_root: Path, version: str, distribution: str) -> Path:
    if distribution not in FORMAL_DISTRIBUTIONS:
        raise RuntimeError(f"invalid formal distribution: {distribution}")
    leaf = "trusted-beta-sensitive" if distribution == "trusted-beta" else "ordinary"
    return releases_root / "releases" / version / "windows-x64" / leaf


def local_test_release_dir(
    releases_root: Path, version: str, distribution: str, build_id: str
) -> Path:
    if distribution not in FORMAL_DISTRIBUTIONS:
        raise RuntimeError(f"invalid local-test distribution: {distribution}")
    channel = "trusted-beta-sensitive" if distribution == "trusted-beta" else "ordinary"
    return releases_root / "_local-test" / version / "windows-x64" / channel / build_id


def installer_name(version: str, *, distribution: str, local_test: bool) -> str:
    parts = [f"agent-friend_{version}_windows_x64"]
    if local_test:
        parts.append("local-test-NOT-FOR-DISTRIBUTION")
    if distribution == "trusted-beta":
        parts.append("trusted-beta-SENSITIVE")
    elif distribution != "ordinary":
        raise RuntimeError(f"invalid distribution: {distribution}")
    return "-".join(parts) + "-setup.exe"


def _load_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "release.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"release manifest must be an object: {path}")
    return value


def validate_release_directory(
    directory: Path, *, formal: bool, releases_root: Path | None = None
) -> dict[str, Any]:
    manifest = _load_manifest(directory)
    required = {
        "schema_version",
        "product",
        "version",
        "platform",
        "build_mode",
        "distribution",
        "publishable",
        "sensitive",
        "created_at",
        "build_id",
        "source",
        "toolchain",
        "installer",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise RuntimeError(f"release manifest missing fields {missing}: {directory}")
    if manifest["schema_version"] != 2 or manifest["product"] not in {
        "agent-friend",
        "agent-friend-installer-smoke",
    }:
        raise RuntimeError(f"unsupported release manifest: {directory}")
    profile_id = manifest.get("profile_id", "production")
    expected_product = (
        "agent-friend" if profile_id == "production" else "agent-friend-installer-smoke"
    )
    if (
        profile_id not in {"production", "installer-smoke"}
        or manifest["product"] != expected_product
    ):
        raise RuntimeError(f"release manifest product/profile mismatch: {directory}")
    if manifest["platform"] != "windows-x64":
        raise RuntimeError(f"unexpected release platform: {directory}")
    SemVer.parse(str(manifest["version"]))
    source = manifest["source"]
    installer = manifest["installer"]
    if not isinstance(source, dict) or not isinstance(installer, dict):
        raise RuntimeError(f"invalid release manifest objects: {directory}")
    if not isinstance(manifest["toolchain"], dict):
        raise RuntimeError(f"invalid release toolchain: {directory}")
    if formal:
        if profile_id != "production":
            raise RuntimeError(f"formal releases require the production profile: {directory}")
        if manifest["build_mode"] != "formal" or manifest["publishable"] is not True:
            raise RuntimeError(f"not a publishable formal release: {directory}")
        if manifest["distribution"] not in FORMAL_DISTRIBUTIONS:
            raise RuntimeError(f"invalid formal distribution: {directory}")
        expected_sensitive = manifest["distribution"] == "trusted-beta"
        if manifest["sensitive"] is not expected_sensitive:
            raise RuntimeError(f"formal release sensitivity mismatch: {directory}")
        if source.get("dirty") is not False:
            raise RuntimeError(f"formal release source must be clean: {directory}")
    else:
        if manifest["build_mode"] != "local-test" or manifest["publishable"] is not False:
            raise RuntimeError(f"not a local-test artifact: {directory}")
    name = installer.get("name")
    if not isinstance(name, str) or Path(name).name != name:
        raise RuntimeError(f"invalid installer name: {directory}")
    setup = directory / name
    if not setup.is_file():
        raise RuntimeError(f"installer is missing: {setup}")
    if setup.stat().st_size != installer.get("size"):
        raise RuntimeError(f"installer size mismatch: {setup}")
    if sha256(setup) != installer.get("sha256"):
        raise RuntimeError(f"installer sha256 mismatch: {setup}")
    if releases_root is not None and "native_symbols" in manifest:
        validate_release_symbols(releases_root, manifest)
    return manifest


def discover_formal_releases(releases_root: Path) -> tuple[list[ReleaseRecord], list[str]]:
    records: list[ReleaseRecord] = []
    warnings: list[str] = []
    base = releases_root / "releases"
    if not base.is_dir():
        return records, warnings
    for manifest_path in sorted(base.glob("*/windows-x64/*/release.json")):
        directory = manifest_path.parent
        try:
            manifest = validate_release_directory(
                directory, formal=True, releases_root=releases_root
            )
            version = SemVer.parse(str(manifest["version"]))
            if directory.parents[1].name != str(version):
                raise RuntimeError("manifest version does not match release directory")
            records.append(
                ReleaseRecord(
                    version=version,
                    distribution=str(manifest["distribution"]),
                    directory=directory,
                    manifest=manifest,
                )
            )
            if "native_symbols" not in manifest:
                warnings.append(f"legacy release has no archived native symbols: {directory}")
        except Exception as exc:
            warnings.append(f"{directory}: {exc}")
    return records, warnings


def latest_formal_version(records: Iterable[ReleaseRecord]) -> SemVer | None:
    values = list(records)
    if not values:
        return None
    latest = max(record.version for record in values)
    ambiguous = {str(record.version) for record in values if record.version == latest}
    if len(ambiguous) > 1:
        raise RuntimeError(
            "ambiguous latest formal version with equal precedence: " + ", ".join(sorted(ambiguous))
        )
    return latest


def publish_installer(
    candidate: Path,
    *,
    releases_root: Path,
    manifest: dict[str, Any],
    local_test: bool,
) -> Path:
    validate_release_symbols(releases_root, manifest)
    version = str(manifest["version"])
    distribution = str(manifest["distribution"])
    build_id = str(manifest["build_id"])
    final_dir = (
        local_test_release_dir(releases_root, version, distribution, build_id)
        if local_test
        else formal_release_dir(releases_root, version, distribution)
    )
    if final_dir.exists():
        raise RuntimeError(f"release artifact already exists and is immutable: {final_dir}")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".publish-", dir=final_dir.parent))
    try:
        name = installer_name(version, distribution=distribution, local_test=local_test)
        setup = temporary / name
        shutil.copy2(candidate, setup)
        digest = sha256(setup)
        manifest = dict(manifest)
        manifest["installer"] = {
            "name": name,
            "size": setup.stat().st_size,
            "sha256": digest,
        }
        (temporary / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="ascii")
        (temporary / "release.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        if local_test:
            (temporary / "LOCAL-TEST-NOT-FOR-DISTRIBUTION.txt").write_text(
                "This installer is a local test artifact and must not be distributed.\n",
                encoding="utf-8",
            )
        if distribution == "trusted-beta":
            (temporary / "SENSITIVE-DO-NOT-PUBLISH.txt").write_text(
                "This installer contains shared long-lived API credentials.\n"
                "Distribute only to explicitly trusted recipients. If leaked, rotate all "
                "provider credentials and rebuild.\n",
                encoding="utf-8",
            )
        validate_release_directory(temporary, formal=not local_test, releases_root=releases_root)
        temporary.rename(final_dir)
        return final_dir / name
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
