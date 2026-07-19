"""Product version parsing and synchronized metadata updates."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


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


def _paths(repo_root: Path) -> dict[str, Path]:
    return {
        "tauri": repo_root / "frontend" / "src-tauri" / "tauri.conf.json",
        "frontend": repo_root / "frontend" / "package.json",
        "cargo": repo_root / "frontend" / "src-tauri" / "Cargo.toml",
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


def read_project_version(repo_root: Path) -> ProjectVersion:
    paths = _paths(repo_root)
    tauri = json.loads(paths["tauri"].read_text(encoding="utf-8"))
    frontend = json.loads(paths["frontend"].read_text(encoding="utf-8"))
    values = {
        "tauri": str(tauri.get("version", "")),
        "frontend": str(frontend.get("version", "")),
        "cargo": _cargo_version(paths["cargo"].read_text(encoding="utf-8")),
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
