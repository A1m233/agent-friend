"""Product version parsing and synchronized metadata updates."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import total_ordering
from pathlib import Path

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
TRUSTED_BETA_CHANNEL_SCHEMA_VERSION = 1


@total_ordering
@dataclass(frozen=True, eq=False)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()
    text: str = ""

    @classmethod
    def parse(cls, value: str) -> SemVer:
        match = SEMVER_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid SemVer: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        build = tuple(match.group(5).split(".")) if match.group(5) else ()
        return cls(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
            prerelease=prerelease,
            build=build,
            text=value,
        )

    def _compare_prerelease(self, other: SemVer) -> int:
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left < right else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        left_core = (self.major, self.minor, self.patch)
        right_core = (other.major, other.minor, other.patch)
        if left_core != right_core:
            return left_core < right_core
        return self._compare_prerelease(other) < 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return False
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class ProjectVersion:
    version: SemVer
    values: dict[str, str]


@dataclass(frozen=True)
class TrustedBetaChannel:
    target_release_version: SemVer
    path: Path


def _require_stable_version(version: SemVer, *, label: str) -> None:
    if version.prerelease or version.build:
        raise RuntimeError(f"{label} must be a stable SemVer without prerelease/build metadata")


def trusted_beta_channel_path(releases_root: Path) -> Path:
    return releases_root.resolve(strict=False) / "channels" / "trusted-beta.json"


def read_trusted_beta_channel(releases_root: Path) -> TrustedBetaChannel | None:
    path = trusted_beta_channel_path(releases_root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"trusted-beta channel configuration is unreadable: {path}") from error
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != TRUSTED_BETA_CHANNEL_SCHEMA_VERSION
    ):
        raise RuntimeError(f"unsupported trusted-beta channel configuration: {path}")
    raw_target = value.get("targetReleaseVersion")
    if not isinstance(raw_target, str):
        raise RuntimeError(f"trusted-beta target release version is missing: {path}")
    try:
        target = SemVer.parse(raw_target)
    except ValueError as error:
        raise RuntimeError(
            f"invalid trusted-beta target release version: {raw_target!r}"
        ) from error
    _require_stable_version(target, label="trusted-beta target release version")
    return TrustedBetaChannel(target_release_version=target, path=path)


def configure_trusted_beta_channel(
    releases_root: Path,
    value: str,
    *,
    minimum_release_version: SemVer,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
) -> TrustedBetaChannel:
    try:
        target = SemVer.parse(value)
    except ValueError as error:
        raise RuntimeError(f"invalid trusted-beta target release version: {value!r}") from error
    _require_stable_version(target, label="trusted-beta target release version")
    if target <= minimum_release_version:
        raise RuntimeError(
            "trusted-beta target release version must be newer than the current/latest stable "
            f"release ({minimum_release_version})"
        )

    path = trusted_beta_channel_path(releases_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            {
                "schemaVersion": TRUSTED_BETA_CHANNEL_SCHEMA_VERSION,
                "targetReleaseVersion": str(target),
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    configured = read_trusted_beta_channel(releases_root)
    if configured is None or configured.target_release_version != target:
        raise RuntimeError("trusted-beta channel configuration verification failed")
    return configured


def next_trusted_beta_version(
    channel: TrustedBetaChannel,
    published_versions: Iterable[SemVer],
    *,
    now: datetime | None = None,
) -> SemVer:
    date = (now or datetime.now(UTC)).strftime("%Y%m%d")
    target = channel.target_release_version
    dated_sequences: list[tuple[str, int]] = []
    for version in published_versions:
        if (version.major, version.minor, version.patch) != (
            target.major,
            target.minor,
            target.patch,
        ):
            continue
        prerelease = version.prerelease
        if (
            len(prerelease) != 3
            or prerelease[0] != "beta"
            or not re.fullmatch(r"[1-9]\d{7}", prerelease[1])
            or not re.fullmatch(r"[1-9]\d*", prerelease[2])
        ):
            raise RuntimeError(
                "trusted-beta channel contains a version outside the governed "
                f"<target>-beta.<YYYYMMDD>.<sequence> scheme: {version}"
            )
        dated_sequences.append((prerelease[1], int(prerelease[2])))

    if dated_sequences:
        latest_date = max(item[0] for item in dated_sequences)
        if latest_date > date:
            raise RuntimeError(
                "system UTC date is earlier than the latest trusted-beta artifact "
                f"({date} < {latest_date}); refusing a non-monotonic version"
            )
    sequence = 1 + max(
        (sequence for published_date, sequence in dated_sequences if published_date == date),
        default=0,
    )
    return SemVer.parse(f"{target.major}.{target.minor}.{target.patch}-beta.{date}.{sequence}")


def _paths(repo_root: Path) -> dict[str, Path]:
    return {
        "tauri": repo_root / "frontend" / "src-tauri" / "tauri.conf.json",
        "frontend": repo_root / "frontend" / "package.json",
        "cargo": repo_root / "frontend" / "src-tauri" / "Cargo.toml",
        "cargo_lock": repo_root / "frontend" / "src-tauri" / "Cargo.lock",
    }


def _cargo_version(text: str) -> str:
    in_package = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_package = line == "[package]"
        elif in_package:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"\s*', line)
            if match:
                return match.group(1)
    raise RuntimeError("Cargo.toml [package].version is missing")


def _cargo_lock_version(text: str) -> str:
    pattern = re.compile(r'(?m)^\[\[package\]\]\r?\nname = "app"\r?\nversion = "([^"]+)"\r?$')
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise RuntimeError("Cargo.lock app package version is missing or ambiguous")
    return matches[0]


def read_project_version(repo_root: Path) -> ProjectVersion:
    paths = _paths(repo_root)
    tauri = json.loads(paths["tauri"].read_text(encoding="utf-8"))
    frontend = json.loads(paths["frontend"].read_text(encoding="utf-8"))
    values = {
        "tauri": str(tauri.get("version", "")),
        "frontend": str(frontend.get("version", "")),
        "cargo": _cargo_version(paths["cargo"].read_text(encoding="utf-8")),
        "cargo_lock": _cargo_lock_version(paths["cargo_lock"].read_text(encoding="utf-8")),
    }
    parsed: dict[str, SemVer] = {}
    for name, value in values.items():
        try:
            parsed[name] = SemVer.parse(value)
        except ValueError as exc:
            raise RuntimeError(f"invalid {name} product version: {value!r}") from exc
    if len(set(values.values())) != 1:
        raise RuntimeError(f"product version mismatch: {values}")
    return ProjectVersion(version=parsed["tauri"], values=values)


def _replace_json_version(text: str, version: str) -> str:
    pattern = re.compile(r'(?m)^(\s*"version"\s*:\s*)"[^"]+"')
    updated, count = pattern.subn(lambda match: f'{match.group(1)}"{version}"', text, count=1)
    if count != 1:
        raise RuntimeError("JSON version field is missing or ambiguous")
    return updated


def _replace_cargo_version(text: str, version: str) -> str:
    lines = text.splitlines(keepends=True)
    in_package = False
    replaced = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("["):
            in_package = stripped == "[package]"
            continue
        if in_package and re.fullmatch(r'version\s*=\s*"[^"]+"\s*', stripped):
            newline = (
                "\r\n" if raw_line.endswith("\r\n") else "\n" if raw_line.endswith("\n") else ""
            )
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            lines[index] = f'{indent}version = "{version}"{newline}'
            replaced = True
            break
    if not replaced:
        raise RuntimeError("Cargo.toml [package].version is missing")
    return "".join(lines)


def _replace_cargo_lock_version(text: str, version: str) -> str:
    pattern = re.compile(r'(?m)^(\[\[package\]\]\r?\nname = "app"\r?\nversion = ")[^"]+("\r?$)')
    updated, count = pattern.subn(lambda match: f"{match.group(1)}{version}{match.group(2)}", text)
    if count != 1:
        raise RuntimeError("Cargo.lock app package version is missing or ambiguous")
    return updated


def set_project_version(
    repo_root: Path,
    value: str,
    *,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
) -> ProjectVersion:
    version = SemVer.parse(value)
    paths = _paths(repo_root)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    texts = {name: content.decode("utf-8") for name, content in originals.items()}
    updated = {
        "tauri": _replace_json_version(texts["tauri"], str(version)),
        "frontend": _replace_json_version(texts["frontend"], str(version)),
        "cargo": _replace_cargo_version(texts["cargo"], str(version)),
        "cargo_lock": _replace_cargo_lock_version(texts["cargo_lock"], str(version)),
    }
    temporary: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for name, target in paths.items():
            descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temp = Path(temp_name)
            temporary[name] = temp
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(updated[name].encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        for name, target in paths.items():
            replace(temporary[name], target)
            replaced.append(name)
        result = read_project_version(repo_root)
        if str(result.version) != str(version):
            raise RuntimeError("product version verification failed after update")
        return result
    except Exception:
        for name in replaced:
            paths[name].write_bytes(originals[name])
        read_project_version(repo_root)
        raise
    finally:
        for temp in temporary.values():
            temp.unlink(missing_ok=True)
