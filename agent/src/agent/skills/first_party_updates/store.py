"""Safe extraction and atomic local state for managed first-party Skills."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

import portalocker

from ..loader import SkillLoader
from ..models import SkillRoot
from .models import FirstPartySkillConfig, FirstPartySkillState, PackageReference

MAX_PACKAGE_FILES = 2_048
MAX_SINGLE_FILE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
SOURCE_METADATA_FIELDS = {
    "schema_version",
    "product_repository",
    "reviewed_product_commit",
    "reviewed_at",
    "product_version",
    "content_sha256",
}
PRODUCT_REPOSITORY = "https://github.com/A1m233/agent-friend"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def regular_package_files(root: Path) -> list[Path]:
    """Return a deterministic regular-file list and reject links/special entries."""

    if not root.is_dir():
        raise ValueError(f"Skill package is not a directory: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(path)):
            raise ValueError(f"links are not allowed in a managed Skill: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"non-regular managed Skill entry: {path}")
        files.append(path)
    return files


def package_snapshot_digest(root: Path, *, exclude_source_metadata: bool = False) -> str:
    """Hash paths and bytes so cache verification is independent of ZIP metadata."""

    digest = hashlib.sha256()
    for path in regular_package_files(root):
        relative = path.relative_to(root).as_posix()
        if exclude_source_metadata and relative == "metadata/source.json":
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class FirstPartySkillStore:
    """Own immutable package directories and the one atomic active-state pointer."""

    def __init__(
        self,
        personal_root: Path,
        *,
        config: FirstPartySkillConfig | None = None,
        loader: SkillLoader | None = None,
    ) -> None:
        self.personal_root = personal_root
        self.config = config or FirstPartySkillConfig()
        self.root = personal_root / ".system" / "first-party" / self.config.skill_name
        self.versions_root = self.root / "versions"
        self.staging_root = self.root / "staging"
        self.state_path = self.root / "state.json"
        self._lock_path = self.root.parent / ".update.lock"
        self._loader = loader or SkillLoader()

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self._lock_path), timeout=30):
            yield

    def load_state(self) -> FirstPartySkillState:
        if not self.state_path.exists():
            return FirstPartySkillState()
        try:
            raw = self.state_path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
            return FirstPartySkillState.from_json(value)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return FirstPartySkillState(
                last_result="state_corrupt",
                last_detail=f"{type(exc).__name__}: {exc}",
            )

    def save_state(self, state: FirstPartySkillState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        raw = (
            json.dumps(
                state.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        temporary = self.root / f".state-{os.getpid()}-{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def package_root(self, reference: PackageReference) -> Path:
        return self.versions_root / reference.package_sha256 / self.config.skill_name

    def resolve_effective(
        self,
    ) -> tuple[Path | None, str, PackageReference | None, FirstPartySkillState]:
        """Resolve only explicitly committed state; never promote orphan cache directories."""

        state = self.load_state()
        for source, reference in (("managed", state.active), ("previous", state.previous)):
            if reference is None:
                continue
            root = self.package_root(reference)
            try:
                self.validate_package(root, reference)
            except (OSError, ValueError):
                continue
            return root, source, reference, state
        return None, "baseline", None, state

    def cached_package_valid(self, reference: PackageReference) -> bool:
        try:
            self.validate_package(self.package_root(reference), reference)
        except (OSError, ValueError):
            return False
        return True

    def install_archive(self, archive: bytes, reference: PackageReference) -> Path:
        """Validate an archive in staging and atomically publish an immutable version."""

        if len(archive) > self.config.max_package_bytes:
            raise ValueError("Skill package exceeds the compressed byte limit")
        actual_archive_digest = hashlib.sha256(archive).hexdigest()
        if actual_archive_digest != reference.package_sha256:
            raise ValueError("Skill package SHA-256 differs from the release manifest")
        target_version = self.versions_root / reference.package_sha256
        target_package = target_version / self.config.skill_name
        if target_version.exists():
            self.validate_package(target_package, reference)
            return target_package

        self.versions_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        staging_version = self.staging_root / (
            f".{reference.package_sha256}-{os.getpid()}-{uuid4().hex}"
        )
        staging_package = staging_version / self.config.skill_name
        staging_package.mkdir(parents=True, exist_ok=False)
        try:
            self._extract_archive(archive, staging_package)
            self.validate_package(staging_package, reference)
            try:
                os.replace(staging_version, target_version)
            except FileExistsError:
                self.validate_package(target_package, reference)
            self.validate_package(target_package, reference)
            return target_package
        finally:
            if staging_version.exists():
                shutil.rmtree(staging_version)

    def validate_package(self, root: Path, reference: PackageReference) -> None:
        if root.name != self.config.skill_name:
            raise ValueError("managed Skill package directory has the wrong name")
        if self.config.sequence_from_tag(reference.release_tag) != reference.release_sequence:
            raise ValueError("managed Skill release tag and sequence differ")
        snapshot = package_snapshot_digest(root)
        if snapshot != reference.package_snapshot_sha256:
            raise ValueError("managed Skill package snapshot digest differs from state")
        self._validate_source_metadata(root)
        catalog = self._loader.load((SkillRoot("builtin", root),), generation=0)
        matching = [record for record in catalog.entries if record.name == self.config.skill_name]
        if len(matching) != 1 or catalog.issues:
            issue_codes = ",".join(issue.code for issue in catalog.issues) or "missing_skill"
            raise ValueError(f"managed Skill package failed loader validation: {issue_codes}")
        if matching[0].package_root != root.resolve(strict=True):
            raise ValueError("managed Skill loader resolved an unexpected package root")

    def _validate_source_metadata(self, root: Path) -> None:
        path = root / "metadata" / "source.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("managed Skill source metadata is invalid") from exc
        if not isinstance(value, dict) or set(value) != SOURCE_METADATA_FIELDS:
            raise ValueError("managed Skill source metadata fields differ from schema 1")
        if value.get("schema_version") != 1:
            raise ValueError("managed Skill source metadata schema is unsupported")
        if value.get("product_repository") != PRODUCT_REPOSITORY:
            raise ValueError("managed Skill was not reviewed against the pinned product")
        commit = value.get("reviewed_product_commit")
        digest = value.get("content_sha256")
        if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
            raise ValueError("managed Skill reviewed product commit is invalid")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError("managed Skill content digest is invalid")
        if digest != package_snapshot_digest(root, exclude_source_metadata=True):
            raise ValueError("managed Skill content digest does not match package files")

    @staticmethod
    def _extract_archive(archive: bytes, destination: Path) -> None:
        seen: set[str] = set()
        expanded = 0
        try:
            bundle = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile as exc:
            raise ValueError("Skill package is not a valid ZIP archive") from exc
        with bundle:
            infos = bundle.infolist()
            if not infos or len(infos) > MAX_PACKAGE_FILES:
                raise ValueError("Skill package file count is outside the allowed range")
            for info in infos:
                relative = FirstPartySkillStore._safe_archive_path(info)
                key = unicodedata.normalize("NFC", relative.as_posix()).casefold()
                if key in seen:
                    raise ValueError("Skill package contains duplicate or case-colliding paths")
                seen.add(key)
                if info.flag_bits & 0x1:
                    raise ValueError("encrypted Skill package entries are not allowed")
                if info.file_size > MAX_SINGLE_FILE_BYTES:
                    raise ValueError("Skill package entry exceeds the single-file limit")
                expanded += info.file_size
                if expanded > MAX_EXPANDED_BYTES:
                    raise ValueError("Skill package exceeds the expanded byte limit")
                target = destination.joinpath(*relative.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                remaining = info.file_size
                with bundle.open(info, "r") as source, target.open("xb") as output:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Skill package entry ended before its declared size")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        raise ValueError("Skill package entry exceeded its declared size")

    @staticmethod
    def _safe_archive_path(info: zipfile.ZipInfo) -> PurePosixPath:
        name = info.filename
        if not name or "\\" in name or name.startswith("/") or "\0" in name:
            raise ValueError("Skill package contains an unsafe path")
        raw_parts = name.rstrip("/").split("/")
        if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError("Skill package contains an unsafe path component")
        for part in raw_parts:
            if (
                ":" in part
                or part.endswith((".", " "))
                or any(ord(character) < 32 for character in part)
                or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
            ):
                raise ValueError("Skill package contains a Windows-ambiguous path")
        relative = PurePosixPath(*raw_parts)
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        allowed_type = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
        if file_type not in {0, allowed_type}:
            raise ValueError("Skill package contains a link or special file")
        return relative
