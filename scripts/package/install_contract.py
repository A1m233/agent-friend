"""Machine contracts for Windows install identity and versioned package payloads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

PROFILE_SCHEMA_VERSION = 1
BUILD_IDENTITY_SCHEMA_VERSION = 1
PAYLOAD_MANIFEST_SCHEMA_VERSION = 1
ACTIVE_INSTALL_SCHEMA_VERSION = 1
INSTALLER_VERIFICATION_SCHEMA_VERSION = 1
INSTALLER_VERIFICATION_SCOPE = "windows-installer-shared-behavior-v1"
WINDOWS_INSTALLER_MANUFACTURER = "agentfriend"

BUILD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)

DOWNGRADE_BLOCKED_ZH_CN = "当前已安装的 agent-friend 是更新的版本，此安装包无法继续"
DOWNGRADE_BLOCKED_EN_US = (
    "A newer version of agent-friend is already installed. This installer cannot continue."
)


class InstalledVersionState(StrEnum):
    ABSENT = "absent"
    UPGRADE = "upgrade"
    REPAIR = "repair"
    DOWNGRADE_BLOCKED = "downgrade-blocked"


class InstallRootTransition(StrEnum):
    NEW_INSTALL = "new-install"
    IN_PLACE = "in-place"
    EXPLICIT_MOVE = "explicit-move"


class PackageVerificationLevel(StrEnum):
    """Minimum package evidence required by the current code change."""

    STANDARD = "standard"
    PACKAGE_SENSITIVE = "package-sensitive"
    FULL_MATRIX = "full-matrix"


PACKAGE_VERIFICATION_LEVEL_ORDER = {
    PackageVerificationLevel.STANDARD: 1,
    PackageVerificationLevel.PACKAGE_SENSITIVE: 2,
    PackageVerificationLevel.FULL_MATRIX: 3,
}


@dataclass(frozen=True)
class InstallProfile:
    schema_version: int
    profile_id: str
    product_name: str
    bundle_identifier: str
    main_binary_name: str
    install_dir_name: str
    registry_product_name: str
    autostart_value_name: str
    data_namespace: str
    settings_namespace: str
    log_namespace: str
    keyring_service: str
    bridge_port: int
    voice_port: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "profileId": self.profile_id,
            "productName": self.product_name,
            "bundleIdentifier": self.bundle_identifier,
            "mainBinaryName": self.main_binary_name,
            "installDirName": self.install_dir_name,
            "registryProductName": self.registry_product_name,
            "autostartValueName": self.autostart_value_name,
            "dataNamespace": self.data_namespace,
            "settingsNamespace": self.settings_namespace,
            "logNamespace": self.log_namespace,
            "keyringService": self.keyring_service,
            "bridgePort": self.bridge_port,
            "voicePort": self.voice_port,
        }

    @classmethod
    def from_json_dict(cls, value: dict[str, Any]) -> InstallProfile:
        expected = resolve_install_profile(str(value.get("profileId", "")))
        if value != expected.to_json_dict():
            raise RuntimeError(f"install profile contract drift for {expected.profile_id!r}")
        return expected


PRODUCTION_PROFILE = InstallProfile(
    schema_version=PROFILE_SCHEMA_VERSION,
    profile_id="production",
    product_name="agent-friend",
    bundle_identifier="com.agentfriend.desktop",
    main_binary_name="agent-friend",
    install_dir_name="agent-friend",
    registry_product_name="agent-friend",
    autostart_value_name="agent-friend",
    data_namespace="agent-friend",
    settings_namespace="com.agentfriend.desktop",
    log_namespace="agent-friend",
    keyring_service="agent-friend",
    bridge_port=18_800,
    voice_port=18_900,
)

INSTALLER_SMOKE_PROFILE = InstallProfile(
    schema_version=PROFILE_SCHEMA_VERSION,
    profile_id="installer-smoke",
    product_name="agent-friend-installer-smoke",
    bundle_identifier="com.agentfriend.desktop.installer-smoke",
    main_binary_name="agent-friend-installer-smoke",
    install_dir_name="agent-friend-installer-smoke",
    registry_product_name="agent-friend-installer-smoke",
    autostart_value_name="agent-friend-installer-smoke",
    data_namespace="agent-friend-installer-smoke",
    settings_namespace="com.agentfriend.desktop.installer-smoke",
    log_namespace="agent-friend-installer-smoke",
    keyring_service="agent-friend-installer-smoke",
    bridge_port=28_800,
    voice_port=28_900,
)

INSTALL_PROFILES = {
    PRODUCTION_PROFILE.profile_id: PRODUCTION_PROFILE,
    INSTALLER_SMOKE_PROFILE.profile_id: INSTALLER_SMOKE_PROFILE,
}

INSTALLER_VERIFICATION_EXCLUDED_PREFIXES = (
    ".claude/",
    ".codex/",
    ".cursor/",
    ".github/",
    "agent_eval/",
    "client/",
    "docs/",
    "experiments/",
    "frontend/src/__tests__/",
    "frontend/src/test/",
    "memory_eval/",
    "tools/",
)
INSTALLER_VERIFICATION_EXCLUDED_FILES = {
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "agents.md",
    "claude.md",
    "interview.md",
    "readme.md",
    "agent/readme.md",
    "agent_bridge/readme.md",
    "frontend/readme.md",
    "llm_providers/readme.md",
    "memory/readme.md",
    "runtime_config/readme.md",
    "scripts/readme.md",
    "voice_bridge/readme.md",
}
INSTALLER_VERIFICATION_REQUIRED_FILES = {
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/src-tauri/cargo.lock",
    "scripts/package/install_contract.py",
    "scripts/package/installer_smoke.py",
    "scripts/package/run.py",
    "scripts/package/windows/nsis-hooks.nsh",
    "uv.lock",
}

# Level 3 changes can alter an existing installation's state transition. They require the full
# isolated upgrade/repair/migration matrix before a production installer can be distributed.
FULL_MATRIX_FILES = {
    "agent_bridge/src/agent_bridge/lifecycle.py",
    "frontend/src-tauri/src/bin/agent-friend-installer-helper.rs",
    "frontend/src-tauri/src/install_contract.rs",
    "frontend/src-tauri/src/runtime_services/supervisor.rs",
    "runtime_config/src/runtime_config/build_identity.py",
    "scripts/package/install_contract.py",
    "scripts/package/installer_smoke.py",
    "scripts/package/releases.py",
    "scripts/package/run.py",
    "scripts/package/versioning.py",
    "scripts/package/windows/nsis-hooks.nsh",
    "scripts/package/windows/tauri.package.conf.json",
    "voice_bridge/src/voice_bridge/lifecycle.py",
}
FULL_MATRIX_PREFIXES = ("scripts/package/windows/installer-",)

# Level 2 changes can alter frozen contents or package bootability without changing how an
# existing installation transitions. They require a real frozen candidate, not the installer
# upgrade matrix.
PACKAGE_SENSITIVE_FILES = {
    "agent/pyproject.toml",
    "agent_bridge/pyproject.toml",
    "llm_providers/pyproject.toml",
    "memory/pyproject.toml",
    "runtime_config/pyproject.toml",
    "scripts/package/dependency_policy.py",
    "scripts/package/run.ps1",
    "scripts/package/run.sh",
    "scripts/package/runtime_entry.py",
    "scripts/package/windows/agent_friend_runtime.spec",
    "uv.lock",
    "voice_bridge/pyproject.toml",
}
PACKAGE_SENSITIVE_PREFIXES = (
    "scripts/package/vendor/",
    "scripts/package/windows/hooks/",
)
FROZEN_RUNTIME_SOURCE_PREFIXES = (
    "agent/src/",
    "agent_bridge/src/",
    "llm_providers/src/",
    "memory/src/",
    "runtime_config/src/",
    "voice_bridge/src/",
)


def _canonical_repository_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if (
        not normalized
        or normalized.startswith("../")
        or "/../" in normalized
        or "\n" in normalized
        or "\r" in normalized
    ):
        raise RuntimeError(f"invalid repository path for installer verification: {value!r}")
    return normalized


def is_installer_verification_input(path: str) -> bool:
    """Return whether a repository file can invalidate installer behavior evidence.

    The exclusion list is intentionally narrow. Unknown files fail safe by participating in
    the fingerprint; only guidance, requirements/evidence, generated instruction surfaces, and
    repository-level explanatory files are excluded.
    """

    normalized = _canonical_repository_path(path).casefold()
    if normalized in INSTALLER_VERIFICATION_EXCLUDED_FILES:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    if (
        "/tests/" in f"/{normalized}/"
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or ".test." in basename
        or ".spec." in basename
    ):
        return False
    return not any(
        normalized.startswith(prefix) for prefix in INSTALLER_VERIFICATION_EXCLUDED_PREFIXES
    )


def package_verification_level_for_path(path: str) -> PackageVerificationLevel:
    """Classify a changed path without conflating ordinary product code with packaging risk."""

    normalized = _canonical_repository_path(path).casefold()
    if not is_installer_verification_input(normalized):
        return PackageVerificationLevel.STANDARD
    if normalized in FULL_MATRIX_FILES or any(
        normalized.startswith(prefix) for prefix in FULL_MATRIX_PREFIXES
    ):
        return PackageVerificationLevel.FULL_MATRIX
    if normalized in PACKAGE_SENSITIVE_FILES or any(
        normalized.startswith(prefix) for prefix in PACKAGE_SENSITIVE_PREFIXES
    ):
        return PackageVerificationLevel.PACKAGE_SENSITIVE
    if normalized.startswith(FROZEN_RUNTIME_SOURCE_PREFIXES):
        suffix = Path(normalized).suffix
        if suffix not in {".py", ".pyi"}:
            return PackageVerificationLevel.PACKAGE_SENSITIVE
    return PackageVerificationLevel.STANDARD


def max_package_verification_level(
    *levels: PackageVerificationLevel | str,
) -> PackageVerificationLevel:
    parsed = [PackageVerificationLevel(level) for level in levels]
    if not parsed:
        return PackageVerificationLevel.STANDARD
    return max(parsed, key=PACKAGE_VERIFICATION_LEVEL_ORDER.__getitem__)


def classify_installer_impact(paths: set[str]) -> dict[str, Any]:
    """Return the independent package trigger and minimum verification level.

    The legacy function name remains the public import used by the package runner. Its machine
    result now models the two decisions separately: ordinary product changes do not force a
    package, while package-sensitive and installer-protocol changes do.
    """

    normalized = sorted({_canonical_repository_path(path) for path in paths})
    by_level = {
        level: [path for path in normalized if package_verification_level_for_path(path) == level]
        for level in PackageVerificationLevel
    }
    minimum = max_package_verification_level(
        *(package_verification_level_for_path(path) for path in normalized)
    )
    return {
        "classification": minimum.value,
        "minimumVerificationLevel": minimum.value,
        "packageRequired": minimum != PackageVerificationLevel.STANDARD,
        "changedPaths": normalized,
        "standardPaths": by_level[PackageVerificationLevel.STANDARD],
        "packageSensitivePaths": by_level[PackageVerificationLevel.PACKAGE_SENSITIVE],
        "fullMatrixPaths": by_level[PackageVerificationLevel.FULL_MATRIX],
        # Compatibility fields for report consumers while they migrate to the explicit levels.
        "installerSensitivePaths": by_level[PackageVerificationLevel.FULL_MATRIX],
        "verificationExcludedPaths": [
            path for path in normalized if not is_installer_verification_input(path)
        ],
    }


@dataclass(frozen=True)
class InstallerVerificationInputs:
    schema_version: int
    scope: str
    package_input_sha256: str
    file_count: int
    build_environment: tuple[tuple[str, str], ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "scope": self.scope,
            "packageInputSha256": self.package_input_sha256,
            "fileCount": self.file_count,
            "buildEnvironment": dict(self.build_environment),
        }

    @classmethod
    def from_json_dict(cls, value: dict[str, Any]) -> InstallerVerificationInputs:
        try:
            environment = value["buildEnvironment"]
            if not isinstance(environment, dict):
                raise TypeError("buildEnvironment must be an object")
            inputs = cls(
                schema_version=int(value["schemaVersion"]),
                scope=str(value["scope"]),
                package_input_sha256=str(value["packageInputSha256"]),
                file_count=int(value["fileCount"]),
                build_environment=tuple(
                    sorted((str(key), str(item)) for key, item in environment.items())
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("invalid installer verification inputs contract") from error
        if inputs.schema_version != INSTALLER_VERIFICATION_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported installer verification inputs schema: {inputs.schema_version}"
            )
        if inputs.scope != INSTALLER_VERIFICATION_SCOPE:
            raise RuntimeError(f"unsupported installer verification scope: {inputs.scope!r}")
        if not SHA256_PATTERN.fullmatch(inputs.package_input_sha256):
            raise RuntimeError("installer verification package input SHA-256 is invalid")
        if inputs.file_count <= 0:
            raise RuntimeError("installer verification input file count must be positive")
        if not inputs.build_environment or any(
            not key or not item for key, item in inputs.build_environment
        ):
            raise RuntimeError("installer verification build environment is incomplete")
        if inputs.to_json_dict() != value:
            raise RuntimeError("installer verification inputs are not canonical")
        return inputs


def build_installer_verification_inputs(
    repo_root: Path,
    *,
    repository_paths: set[str],
    build_environment: dict[str, str],
) -> InstallerVerificationInputs:
    root = repo_root.resolve(strict=True)
    normalized_paths = {_canonical_repository_path(path) for path in repository_paths}
    included_paths = sorted(
        path for path in normalized_paths if is_installer_verification_input(path)
    )
    folded_included = {path.casefold() for path in included_paths}
    missing_required = sorted(INSTALLER_VERIFICATION_REQUIRED_FILES - folded_included)
    if missing_required:
        raise RuntimeError(
            "installer verification input inventory is missing required files: "
            + ", ".join(missing_required)
        )
    environment = {str(key): str(value) for key, value in sorted(build_environment.items())}
    if not environment or any(not key or not value for key, value in environment.items()):
        raise RuntimeError("installer verification build environment is incomplete")
    resolved_files: dict[str, Path] = {}
    missing_files: set[str] = set()
    for relative in included_paths:
        path = (root / Path(relative)).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise RuntimeError(
                f"installer verification input escaped repository root: {relative}"
            ) from error
        if not path.exists():
            missing_files.add(relative)
            continue
        if not path.is_file():
            raise RuntimeError(f"installer verification input is not a file: {relative}")
        resolved_files[relative] = path
    git_object_format, blob_oids = _git_filtered_blob_oids(
        root,
        tuple(resolved_files),
    )
    files = [
        {"path": relative, "missing": True}
        if relative in missing_files
        else {"path": relative, "gitBlobOid": blob_oids[relative]}
        for relative in included_paths
    ]
    manifest = {
        "schemaVersion": INSTALLER_VERIFICATION_SCHEMA_VERSION,
        "scope": INSTALLER_VERIFICATION_SCOPE,
        "gitObjectFormat": git_object_format,
        "buildEnvironment": environment,
        "files": files,
    }
    return InstallerVerificationInputs(
        schema_version=INSTALLER_VERIFICATION_SCHEMA_VERSION,
        scope=INSTALLER_VERIFICATION_SCOPE,
        package_input_sha256=sha256_bytes(canonical_json_bytes(manifest)),
        file_count=len(files),
        build_environment=tuple(environment.items()),
    )


def _git_filtered_blob_oids(
    root: Path,
    relative_paths: tuple[str, ...],
) -> tuple[str, dict[str, str]]:
    """Hash working files after Git clean filters, independent of checkout line endings."""

    object_format_process = subprocess.run(
        ["git", "rev-parse", "--show-object-format"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    object_format = object_format_process.stdout.strip()
    if object_format not in {"sha1", "sha256"}:
        raise RuntimeError(f"unsupported Git object format: {object_format!r}")
    if not relative_paths:
        return object_format, {}
    process = subprocess.run(
        ["git", "hash-object", "--stdin-paths"],
        cwd=root,
        input="".join(f"{relative}\n" for relative in relative_paths),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    object_ids = process.stdout.splitlines()
    expected_length = 40 if object_format == "sha1" else 64
    if len(object_ids) != len(relative_paths) or any(
        re.fullmatch(rf"[0-9a-f]{{{expected_length}}}", object_id) is None
        for object_id in object_ids
    ):
        raise RuntimeError("Git returned invalid installer verification blob identities")
    return object_format, dict(zip(relative_paths, object_ids, strict=True))


def _semver_key(value: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]]:
    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise RuntimeError(f"invalid semantic version: {value!r}")
    prerelease = match.group("prerelease")
    identifiers: tuple[tuple[int, int | str], ...]
    if prerelease is None:
        identifiers = ((2, ""),)
    else:
        parsed: list[tuple[int, int | str]] = []
        for identifier in prerelease.split("."):
            parsed.append((0, int(identifier)) if identifier.isdigit() else (1, identifier))
        identifiers = tuple(parsed)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        identifiers,
    )


def classify_installed_version(
    installed_version: str | None,
    candidate_version: str,
) -> InstalledVersionState:
    candidate = _semver_key(candidate_version)
    if installed_version is None:
        return InstalledVersionState.ABSENT
    installed = _semver_key(installed_version)
    if installed < candidate:
        return InstalledVersionState.UPGRADE
    if installed == candidate:
        return InstalledVersionState.REPAIR
    return InstalledVersionState.DOWNGRADE_BLOCKED


def canonical_windows_install_root(value: str) -> str:
    normalized = value.strip().strip('"').replace("/", "\\")
    if not re.fullmatch(r"[A-Za-z]:\\.+", normalized):
        raise RuntimeError(f"invalid absolute Windows install root: {value!r}")
    return normalized.rstrip("\\").casefold()


def classify_install_root_transition(
    registered_root: str | None,
    target_root: str,
    *,
    move_intent: bool,
) -> InstallRootTransition:
    target = canonical_windows_install_root(target_root)
    if registered_root is None:
        return InstallRootTransition.NEW_INSTALL
    registered = canonical_windows_install_root(registered_root)
    if registered == target:
        return InstallRootTransition.IN_PLACE
    if not move_intent:
        raise RuntimeError("install_root_drift_requires_explicit_move")
    return InstallRootTransition.EXPLICIT_MOVE


@dataclass(frozen=True)
class BuildIdentity:
    schema_version: int
    product_version: str
    build_id: str
    source_commit: str
    source_tree_sha256: str
    dirty: bool
    build_mode: str
    distribution: str
    profile_id: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "productVersion": self.product_version,
            "buildId": self.build_id,
            "sourceCommit": self.source_commit,
            "sourceTreeSha256": self.source_tree_sha256,
            "dirty": self.dirty,
            "buildMode": self.build_mode,
            "distribution": self.distribution,
            "profileId": self.profile_id,
        }

    @classmethod
    def from_json_dict(cls, value: dict[str, Any]) -> BuildIdentity:
        try:
            identity = cls(
                schema_version=int(value["schemaVersion"]),
                product_version=str(value["productVersion"]),
                build_id=str(value["buildId"]),
                source_commit=str(value["sourceCommit"]),
                source_tree_sha256=str(value["sourceTreeSha256"]),
                dirty=value["dirty"],
                build_mode=str(value["buildMode"]),
                distribution=str(value["distribution"]),
                profile_id=str(value["profileId"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("invalid build identity contract") from error
        if identity.schema_version != BUILD_IDENTITY_SCHEMA_VERSION:
            raise RuntimeError(f"unsupported build identity schema: {identity.schema_version}")
        if not BUILD_ID_PATTERN.fullmatch(identity.build_id):
            raise RuntimeError(f"invalid build id: {identity.build_id!r}")
        if not isinstance(identity.dirty, bool):
            raise RuntimeError("build identity dirty flag must be a boolean")
        if not SHA256_PATTERN.fullmatch(identity.source_tree_sha256):
            raise RuntimeError("build identity source tree SHA-256 is invalid")
        resolve_install_profile(identity.profile_id)
        return identity


@dataclass(frozen=True)
class PayloadFile:
    path: str
    size: int
    sha256: str
    capability: str


@dataclass(frozen=True)
class PayloadManifest:
    schema_version: int
    build_identity_sha256: str
    profile_id: str
    files: tuple[PayloadFile, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "buildIdentitySha256": self.build_identity_sha256,
            "profileId": self.profile_id,
            "files": [asdict(item) for item in self.files],
        }

    @classmethod
    def from_json_dict(cls, value: dict[str, Any]) -> PayloadManifest:
        try:
            files = tuple(
                PayloadFile(
                    path=str(item["path"]),
                    size=int(item["size"]),
                    sha256=str(item["sha256"]),
                    capability=str(item["capability"]),
                )
                for item in value["files"]
            )
            manifest = cls(
                schema_version=int(value["schemaVersion"]),
                build_identity_sha256=str(value["buildIdentitySha256"]),
                profile_id=str(value["profileId"]),
                files=files,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("invalid payload manifest contract") from error
        if manifest.schema_version != PAYLOAD_MANIFEST_SCHEMA_VERSION:
            raise RuntimeError(f"unsupported payload manifest schema: {manifest.schema_version}")
        if not SHA256_PATTERN.fullmatch(manifest.build_identity_sha256):
            raise RuntimeError("payload manifest identity SHA-256 is invalid")
        return manifest


@dataclass(frozen=True)
class ActiveInstallReceipt:
    schema_version: int
    profile_id: str
    build_identity_sha256: str
    build_id: str
    payload_relative_path: str
    payload_manifest_sha256: str
    activated_at: str
    transaction_id: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "profileId": self.profile_id,
            "buildIdentitySha256": self.build_identity_sha256,
            "buildId": self.build_id,
            "payloadRelativePath": self.payload_relative_path,
            "payloadManifestSha256": self.payload_manifest_sha256,
            "activatedAt": self.activated_at,
            "transactionId": self.transaction_id,
        }


def resolve_install_profile(profile_id: str) -> InstallProfile:
    try:
        return INSTALL_PROFILES[profile_id]
    except KeyError as error:
        allowed = ", ".join(sorted(INSTALL_PROFILES))
        raise RuntimeError(
            f"unsupported install profile {profile_id!r}; expected {allowed}"
        ) from error


def validate_profile_isolation() -> None:
    production = PRODUCTION_PROFILE.to_json_dict()
    smoke = INSTALLER_SMOKE_PROFILE.to_json_dict()
    shared = {"schemaVersion"}
    collisions = [key for key in production if key not in shared and production[key] == smoke[key]]
    if collisions:
        raise RuntimeError(
            "installer-smoke profile collides with production fields: " + ", ".join(collisions)
        )


def build_identity(
    *,
    product_version: str,
    build_id: str,
    source: dict[str, Any],
    local_test: bool,
    trusted_beta: bool,
    profile: InstallProfile,
) -> BuildIdentity:
    if not BUILD_ID_PATTERN.fullmatch(build_id):
        raise RuntimeError(f"invalid build id: {build_id!r}")
    commit = source.get("commit")
    source_tree_sha256 = source.get("tree_sha256")
    dirty = source.get("dirty")
    if not isinstance(commit, str) or not commit:
        raise RuntimeError("build source commit is missing")
    if not isinstance(dirty, bool):
        raise RuntimeError("build source dirty flag is missing")
    if not isinstance(source_tree_sha256, str) or not SHA256_PATTERN.fullmatch(source_tree_sha256):
        raise RuntimeError("build source tree SHA-256 is missing or invalid")
    return BuildIdentity(
        schema_version=BUILD_IDENTITY_SCHEMA_VERSION,
        product_version=product_version,
        build_id=build_id,
        source_commit=commit,
        source_tree_sha256=source_tree_sha256,
        dirty=dirty,
        build_mode="local-test" if local_test else "formal",
        distribution="trusted-beta" if trusted_beta else "ordinary",
        profile_id=profile.profile_id,
    )


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def read_build_identity(path: Path) -> BuildIdentity:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not read build identity: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"build identity must be an object: {path}")
    return BuildIdentity.from_json_dict(value)


def read_payload_manifest(path: Path) -> PayloadManifest:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not read payload manifest: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"payload manifest must be an object: {path}")
    return PayloadManifest.from_json_dict(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_canonical_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def safe_relative_path(path: str) -> str:
    candidate = path.replace("\\", "/")
    pure = Path(candidate)
    if not candidate or pure.is_absolute() or candidate.startswith("/"):
        raise RuntimeError(f"payload path must be relative: {path!r}")
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"payload path is unsafe: {path!r}")
    return "/".join(parts)


def capability_for_path(path: str) -> str:
    if path == "build-identity.json":
        return "identity"
    if path.startswith("runtime/python/"):
        return "runtime-python"
    if path.startswith("runtime/models/"):
        return "runtime-models"
    if path.startswith("runtime/vendor/"):
        return "runtime-vendor"
    if path.startswith("runtime/connectors/"):
        return "runtime-connectors"
    if path.startswith("avatar-seed/"):
        return "avatar-seed"
    return "runtime-support"


def build_payload_manifest(root: Path, identity: BuildIdentity) -> PayloadManifest:
    payload_root = root.resolve(strict=True)
    identity_path = payload_root / "build-identity.json"
    write_canonical_json(identity_path, identity.to_json_dict())
    identity_hash = sha256_file(identity_path)
    files: list[PayloadFile] = []
    folded_paths: set[str] = set()
    for file_path in sorted(item for item in payload_root.rglob("*") if item.is_file()):
        relative = safe_relative_path(file_path.relative_to(payload_root).as_posix())
        if relative == "payload-manifest.json":
            continue
        folded = os.path.normcase(relative).casefold()
        if folded in folded_paths:
            raise RuntimeError(f"payload contains a case-folded duplicate path: {relative}")
        folded_paths.add(folded)
        files.append(
            PayloadFile(
                path=relative,
                size=file_path.stat().st_size,
                sha256=sha256_file(file_path),
                capability=capability_for_path(relative),
            )
        )
    if not any(item.path == "build-identity.json" for item in files):
        raise RuntimeError("payload build identity is missing from its manifest")
    return PayloadManifest(
        schema_version=PAYLOAD_MANIFEST_SCHEMA_VERSION,
        build_identity_sha256=identity_hash,
        profile_id=identity.profile_id,
        files=tuple(files),
    )


def write_payload_manifest(root: Path, identity: BuildIdentity) -> PayloadManifest:
    manifest = build_payload_manifest(root, identity)
    write_canonical_json(root / "payload-manifest.json", manifest.to_json_dict())
    return manifest


def validate_payload_manifest(root: Path, manifest: PayloadManifest) -> None:
    payload_root = root.resolve(strict=True)
    seen: set[str] = set()
    for item in manifest.files:
        relative = safe_relative_path(item.path)
        folded = relative.casefold()
        if folded in seen:
            raise RuntimeError(f"payload manifest has a duplicate path: {relative}")
        seen.add(folded)
        if not SHA256_PATTERN.fullmatch(item.sha256):
            raise RuntimeError(f"payload manifest has an invalid SHA-256: {relative}")
        path = (payload_root / Path(relative)).resolve(strict=False)
        try:
            path.relative_to(payload_root)
        except ValueError as error:
            raise RuntimeError(f"payload manifest escaped its root: {relative}") from error
        if not path.is_file():
            raise RuntimeError(f"payload file is missing: {relative}")
        if path.stat().st_size != item.size:
            raise RuntimeError(f"payload file size mismatch: {relative}")
        if sha256_file(path) != item.sha256:
            raise RuntimeError(f"payload file hash mismatch: {relative}")
    identity_path = payload_root / "build-identity.json"
    if sha256_file(identity_path) != manifest.build_identity_sha256:
        raise RuntimeError("payload build identity hash mismatch")


def payload_install_relative_path(build_id: str) -> str:
    if not BUILD_ID_PATTERN.fullmatch(build_id):
        raise RuntimeError(f"invalid build id: {build_id!r}")
    return f"packages/{build_id}"
