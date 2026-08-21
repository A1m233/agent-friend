"""Strict release and local-state models for first-party Skill updates."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _required_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _required_int(value: object, field: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True, order=True)
class SemVer:
    """The deliberately small host compatibility version used by schema 1."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemVer:
        match = _SEMVER_RE.fullmatch(value)
        if match is None:
            raise ValueError("product version must use MAJOR.MINOR.PATCH")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class FirstPartySkillConfig:
    """One product-pinned first-party update source."""

    owner: str = "A1m233"
    repository: str = "agent-friend-guide"
    skill_name: str = "agent-friend-guide"
    manifest_asset_name: str = "agent-friend-guide.manifest.json"
    package_asset_name: str = "agent-friend-guide.zip"
    release_limit: int = 30
    interval_seconds: float = 24 * 60 * 60
    max_manifest_bytes: int = 64 * 1024
    max_package_bytes: int = 16 * 1024 * 1024

    @property
    def releases_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.owner}/{self.repository}/releases"
            f"?per_page={self.release_limit}"
        )

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}"

    def sequence_from_tag(self, tag: str) -> int | None:
        match = re.fullmatch(rf"{re.escape(self.skill_name)}-r([1-9][0-9]*)", tag)
        return int(match.group(1)) if match is not None else None


@dataclass(frozen=True)
class ReleaseAsset:
    """The subset of GitHub release asset metadata used as an integrity anchor."""

    asset_id: int
    name: str
    api_url: str
    size: int
    digest: str

    @classmethod
    def from_api(cls, value: object) -> ReleaseAsset:
        if not isinstance(value, dict):
            raise ValueError("release asset must be an object")
        raw_digest = _required_str(value.get("digest"), "asset.digest")
        if not raw_digest.startswith("sha256:") or not _SHA256_RE.fullmatch(raw_digest[7:]):
            raise ValueError("asset.digest must be sha256:<lowercase digest>")
        return cls(
            asset_id=_required_int(value.get("id"), "asset.id", minimum=1),
            name=_required_str(value.get("name"), "asset.name"),
            api_url=_required_str(value.get("url"), "asset.url"),
            size=_required_int(value.get("size"), "asset.size"),
            digest=raw_digest[7:],
        )


@dataclass(frozen=True)
class ReleaseInfo:
    """A stable immutable GitHub release and its declared assets."""

    release_id: int
    tag: str
    target_commitish: str
    immutable: bool
    draft: bool
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]

    @classmethod
    def from_api(cls, value: object) -> ReleaseInfo:
        if not isinstance(value, dict):
            raise ValueError("release must be an object")
        raw_assets = value.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("release.assets must be an array")
        for field in ("immutable", "draft", "prerelease"):
            if not isinstance(value.get(field), bool):
                raise ValueError(f"release.{field} must be a boolean")
        return cls(
            release_id=_required_int(value.get("id"), "release.id", minimum=1),
            tag=_required_str(value.get("tag_name"), "release.tag_name"),
            target_commitish=_required_str(
                value.get("target_commitish"), "release.target_commitish"
            ),
            immutable=bool(value["immutable"]),
            draft=bool(value["draft"]),
            prerelease=bool(value["prerelease"]),
            assets=tuple(ReleaseAsset.from_api(item) for item in raw_assets),
        )

    def asset(self, name: str) -> ReleaseAsset | None:
        matches = [asset for asset in self.assets if asset.name == name]
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class ReleasePage:
    """One conditional GitHub release-list response."""

    not_modified: bool
    etag: str | None
    releases: tuple[ReleaseInfo, ...] = ()


@dataclass(frozen=True)
class ReleaseManifest:
    """Schema 1 manifest attached beside the immutable Skill package."""

    schema_version: int
    skill_name: str
    release_sequence: int
    release_tag: str
    source_commit: str
    package_sha256: str
    package_snapshot_sha256: str
    package_size: int
    min_product_version: SemVer
    max_product_version_exclusive: SemVer

    @classmethod
    def from_json(cls, value: object) -> ReleaseManifest:
        if not isinstance(value, dict):
            raise ValueError("release manifest must be an object")
        required = {
            "schema_version",
            "skill_name",
            "release_sequence",
            "release_tag",
            "source_commit",
            "package_sha256",
            "package_snapshot_sha256",
            "package_size",
            "min_product_version",
            "max_product_version_exclusive",
        }
        if set(value) != required:
            raise ValueError("release manifest fields differ from schema 1")
        schema_version = _required_int(value["schema_version"], "schema_version", minimum=1)
        if schema_version != 1:
            raise ValueError("unsupported release manifest schema")
        source_commit = _required_str(value["source_commit"], "source_commit")
        package_sha256 = _required_str(value["package_sha256"], "package_sha256")
        snapshot_sha256 = _required_str(value["package_snapshot_sha256"], "package_snapshot_sha256")
        if not _COMMIT_RE.fullmatch(source_commit):
            raise ValueError("source_commit must be a full lowercase Git commit")
        if not _SHA256_RE.fullmatch(package_sha256):
            raise ValueError("package_sha256 must be a lowercase SHA-256 digest")
        if not _SHA256_RE.fullmatch(snapshot_sha256):
            raise ValueError("package_snapshot_sha256 must be a lowercase SHA-256 digest")
        minimum = SemVer.parse(_required_str(value["min_product_version"], "min_product_version"))
        maximum = SemVer.parse(
            _required_str(
                value["max_product_version_exclusive"],
                "max_product_version_exclusive",
            )
        )
        if maximum <= minimum:
            raise ValueError("product compatibility range must be non-empty")
        return cls(
            schema_version=schema_version,
            skill_name=_required_str(value["skill_name"], "skill_name"),
            release_sequence=_required_int(
                value["release_sequence"], "release_sequence", minimum=1
            ),
            release_tag=_required_str(value["release_tag"], "release_tag"),
            source_commit=source_commit,
            package_sha256=package_sha256,
            package_snapshot_sha256=snapshot_sha256,
            package_size=_required_int(value["package_size"], "package_size", minimum=1),
            min_product_version=minimum,
            max_product_version_exclusive=maximum,
        )

    def compatible_with(self, host: SemVer) -> bool:
        return self.min_product_version <= host < self.max_product_version_exclusive

    def validate_release(self, release: ReleaseInfo, config: FirstPartySkillConfig) -> None:
        sequence = config.sequence_from_tag(release.tag)
        if sequence is None or sequence != self.release_sequence:
            raise ValueError("release tag and manifest sequence differ")
        if self.release_tag != release.tag:
            raise ValueError("release tag and manifest tag differ")
        if self.skill_name != config.skill_name:
            raise ValueError("manifest Skill name differs from pinned Skill")
        if self.source_commit != release.target_commitish:
            raise ValueError("manifest source commit differs from release target")


@dataclass(frozen=True)
class PackageReference:
    """A locally committed immutable package selected by one trusted release."""

    release_sequence: int
    release_id: int
    release_tag: str
    source_commit: str
    package_sha256: str
    package_snapshot_sha256: str
    min_product_version: str
    max_product_version_exclusive: str

    @classmethod
    def from_manifest(cls, manifest: ReleaseManifest, *, release_id: int) -> PackageReference:
        return cls(
            release_sequence=manifest.release_sequence,
            release_id=release_id,
            release_tag=manifest.release_tag,
            source_commit=manifest.source_commit,
            package_sha256=manifest.package_sha256,
            package_snapshot_sha256=manifest.package_snapshot_sha256,
            min_product_version=str(manifest.min_product_version),
            max_product_version_exclusive=str(manifest.max_product_version_exclusive),
        )

    @classmethod
    def from_json(cls, value: object) -> PackageReference:
        if not isinstance(value, dict):
            raise ValueError("package reference must be an object")
        required = {field.name for field in cls.__dataclass_fields__.values()}
        if set(value) != required:
            raise ValueError("package reference fields differ from schema")
        source_commit = _required_str(value["source_commit"], "source_commit")
        package_sha256 = _required_str(value["package_sha256"], "package_sha256")
        snapshot_sha256 = _required_str(value["package_snapshot_sha256"], "package_snapshot_sha256")
        if not _COMMIT_RE.fullmatch(source_commit):
            raise ValueError("source_commit must be a full lowercase Git commit")
        if not _SHA256_RE.fullmatch(package_sha256) or not _SHA256_RE.fullmatch(snapshot_sha256):
            raise ValueError("package reference digests must be lowercase SHA-256")
        minimum = SemVer.parse(_required_str(value["min_product_version"], "min_product_version"))
        maximum = SemVer.parse(
            _required_str(
                value["max_product_version_exclusive"],
                "max_product_version_exclusive",
            )
        )
        if maximum <= minimum:
            raise ValueError("package reference compatibility range must be non-empty")
        return cls(
            release_sequence=_required_int(
                value["release_sequence"], "release_sequence", minimum=1
            ),
            release_id=_required_int(value["release_id"], "release_id", minimum=1),
            release_tag=_required_str(value["release_tag"], "release_tag"),
            source_commit=source_commit,
            package_sha256=package_sha256,
            package_snapshot_sha256=snapshot_sha256,
            min_product_version=str(minimum),
            max_product_version_exclusive=str(maximum),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "release_sequence": self.release_sequence,
            "release_id": self.release_id,
            "release_tag": self.release_tag,
            "source_commit": self.source_commit,
            "package_sha256": self.package_sha256,
            "package_snapshot_sha256": self.package_snapshot_sha256,
            "min_product_version": self.min_product_version,
            "max_product_version_exclusive": self.max_product_version_exclusive,
        }


@dataclass(frozen=True)
class FirstPartySkillState:
    """Atomically persisted selection state; package bytes live in immutable directories."""

    schema_version: int = 1
    highest_seen_sequence: int = 0
    highest_seen_release_id: int | None = None
    highest_seen_tag: str | None = None
    active: PackageReference | None = None
    previous: PackageReference | None = None
    etag: str | None = None
    last_checked_product_version: str | None = None
    last_checked_at: str | None = None
    last_result: str = "not_checked"
    last_detail: str | None = None

    @classmethod
    def from_json(cls, value: object) -> FirstPartySkillState:
        if not isinstance(value, dict):
            raise ValueError("first-party state must be an object")
        required = {field.name for field in cls.__dataclass_fields__.values()}
        if set(value) != required or value.get("schema_version") != 1:
            raise ValueError("first-party state fields differ from schema 1")
        highest = _required_int(value["highest_seen_sequence"], "highest_seen_sequence", minimum=0)
        release_id_raw = value["highest_seen_release_id"]
        release_id = (
            None
            if release_id_raw is None
            else _required_int(release_id_raw, "highest_seen_release_id", minimum=1)
        )
        for field in (
            "highest_seen_tag",
            "etag",
            "last_checked_product_version",
            "last_checked_at",
            "last_detail",
        ):
            if value[field] is not None and not isinstance(value[field], str):
                raise ValueError(f"{field} must be a string or null")
        active = None if value["active"] is None else PackageReference.from_json(value["active"])
        previous = (
            None if value["previous"] is None else PackageReference.from_json(value["previous"])
        )
        highest_tag = value["highest_seen_tag"]
        if highest == 0 and (release_id is not None or highest_tag is not None):
            raise ValueError("empty first-party state cannot have a highest release identity")
        if highest > 0 and (release_id is None or highest_tag is None):
            raise ValueError("highest first-party sequence requires a release identity")
        if active is not None and active.release_sequence > highest:
            raise ValueError("active package sequence exceeds highest seen sequence")
        if previous is not None and previous.release_sequence > highest:
            raise ValueError("previous package sequence exceeds highest seen sequence")
        return cls(
            schema_version=1,
            highest_seen_sequence=highest,
            highest_seen_release_id=release_id,
            highest_seen_tag=highest_tag,
            active=active,
            previous=previous,
            etag=value["etag"],
            last_checked_product_version=value["last_checked_product_version"],
            last_checked_at=value["last_checked_at"],
            last_result=_required_str(value["last_result"], "last_result"),
            last_detail=value["last_detail"],
        )

    def with_result(
        self,
        *,
        checked_at: str,
        result: str,
        detail: str | None = None,
        etag: str | None = None,
        product_version: str,
    ) -> FirstPartySkillState:
        return replace(
            self,
            last_checked_at=checked_at,
            last_result=result,
            last_detail=detail,
            etag=self.etag if etag is None else etag,
            last_checked_product_version=product_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "highest_seen_sequence": self.highest_seen_sequence,
            "highest_seen_release_id": self.highest_seen_release_id,
            "highest_seen_tag": self.highest_seen_tag,
            "active": self.active.to_dict() if self.active is not None else None,
            "previous": self.previous.to_dict() if self.previous is not None else None,
            "etag": self.etag,
            "last_checked_product_version": self.last_checked_product_version,
            "last_checked_at": self.last_checked_at,
            "last_result": self.last_result,
            "last_detail": self.last_detail,
        }


@dataclass(frozen=True)
class UpdateDiagnostic:
    """A body-free status projection suitable for logs and the Skill route."""

    enabled: bool
    source: str
    active_sequence: int | None
    active_package_sha256: str | None
    highest_seen_sequence: int
    last_checked_at: str | None
    last_result: str
    last_detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "source": self.source,
            "activeSequence": self.active_sequence,
            "activePackageSha256": self.active_package_sha256,
            "highestSeenSequence": self.highest_seen_sequence,
            "lastCheckedAt": self.last_checked_at,
            "lastResult": self.last_result,
            "lastDetail": self.last_detail,
        }
