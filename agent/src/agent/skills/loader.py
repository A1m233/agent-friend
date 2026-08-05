"""Bounded, failure-isolating discovery for Agent Skills compatible packages."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .models import (
    SkillAlias,
    SkillCatalogSnapshot,
    SkillLoadIssue,
    SkillRecord,
    SkillRoot,
)

MAX_DEPTH = 6
MAX_DIRECTORIES = 2_000
MAX_ENTRIES = 20_000
MAX_SKILL_BYTES = 256 * 1024
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SOURCE_RANK = {"builtin": 0, "personal": 1, "shared": 2}


@dataclass
class _ScanBudget:
    directories: int = 0
    entries: int = 0
    exhausted: bool = False


def canonical_key(path: Path) -> str:
    """Return a cross-platform identity key for an already-resolved path."""
    return os.path.normcase(path.as_uri())


def skill_id_for_path(path: Path) -> str:
    digest = hashlib.sha256(canonical_key(path).encode("utf-8")).hexdigest()
    return f"skill_{digest}"


class SkillLoader:
    """Discover and validate Skills without letting one bad package fail the catalog."""

    def __init__(
        self,
        *,
        max_depth: int = MAX_DEPTH,
        max_directories: int = MAX_DIRECTORIES,
        max_entries: int = MAX_ENTRIES,
        max_skill_bytes: int = MAX_SKILL_BYTES,
    ) -> None:
        self._max_depth = max_depth
        self._max_directories = max_directories
        self._max_entries = max_entries
        self._max_skill_bytes = max_skill_bytes

    def load(self, roots: tuple[SkillRoot, ...], *, generation: int = 0) -> SkillCatalogSnapshot:
        records: list[SkillRecord] = []
        issues: list[SkillLoadIssue] = []
        external_roots: set[Path] = set()
        seen_directories: set[str] = set()
        budget = _ScanBudget()

        for root in roots:
            if not root.path.exists():
                continue
            self._walk(
                root,
                root.path,
                depth=0,
                seen_directories=seen_directories,
                budget=budget,
                records=records,
                issues=issues,
                external_roots=external_roots,
            )
            if budget.exhausted:
                break

        deduped = self._dedupe(records, issues)
        ordered = tuple(
            sorted(
                deduped,
                key=lambda record: (
                    _SOURCE_RANK[record.source],
                    record.name,
                    canonical_key(record.canonical_entry_path),
                ),
            )
        )
        fingerprint = hashlib.sha256(
            "\n".join(
                f"{record.id}\0{record.version}\0{record.name}\0{record.description}"
                for record in ordered
            ).encode("utf-8")
        ).hexdigest()
        return SkillCatalogSnapshot(
            generation=generation,
            fingerprint=fingerprint,
            entries=ordered,
            issues=tuple(issues),
            external_watch_roots=tuple(sorted(external_roots, key=canonical_key)),
        )

    def _walk(
        self,
        root: SkillRoot,
        directory: Path,
        *,
        depth: int,
        seen_directories: set[str],
        budget: _ScanBudget,
        records: list[SkillRecord],
        issues: list[SkillLoadIssue],
        external_roots: set[Path],
    ) -> None:
        if depth > self._max_depth or budget.exhausted:
            return
        try:
            canonical_directory = directory.resolve(strict=True)
        except OSError as exc:
            issues.append(self._issue("canonicalize_failed", directory, root, exc))
            return
        directory_key = canonical_key(canonical_directory)
        if directory_key in seen_directories:
            linked_entry = directory / "SKILL.md"
            if self._is_link(directory) and linked_entry.is_file():
                record = self._parse_candidate(root, linked_entry, issues)
                if record is not None:
                    records.append(record)
            if directory != root.path:
                issues.append(
                    SkillLoadIssue(
                        code="link_cycle",
                        message="目录链接形成循环或重复访问，已跳过。",
                        path=directory,
                        source=root.source,
                    )
                )
            return
        seen_directories.add(directory_key)
        budget.directories += 1
        if budget.directories > self._max_directories:
            self._exhaust(root, directory, budget, issues)
            return

        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            code = "root_unreadable" if depth == 0 else "scan_unreadable"
            issues.append(self._issue(code, directory, root, exc))
            return

        for entry in entries:
            budget.entries += 1
            if budget.entries > self._max_entries:
                self._exhaust(root, Path(entry.path), budget, issues)
                return
            path = Path(entry.path)
            if root.source != "builtin" and entry.name.startswith("."):
                continue
            try:
                if entry.is_file(follow_symlinks=True) and entry.name == "SKILL.md":
                    record = self._parse_candidate(root, path, issues)
                    if record is not None:
                        records.append(record)
                    continue
                if not entry.is_dir(follow_symlinks=True):
                    continue
            except OSError as exc:
                issues.append(self._issue("broken_link", path, root, exc))
                continue

            if self._is_link(path):
                try:
                    target = path.resolve(strict=True)
                except OSError as exc:
                    issues.append(self._issue("broken_link", path, root, exc))
                    continue
                try:
                    target.relative_to(root.path.resolve(strict=True))
                except ValueError:
                    external_roots.add(target)
                except OSError as exc:
                    issues.append(self._issue("canonicalize_failed", path, root, exc))
                    continue
            self._walk(
                root,
                path,
                depth=depth + 1,
                seen_directories=seen_directories,
                budget=budget,
                records=records,
                issues=issues,
                external_roots=external_roots,
            )

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(path))

    def _parse_candidate(
        self,
        root: SkillRoot,
        entry_path: Path,
        issues: list[SkillLoadIssue],
    ) -> SkillRecord | None:
        try:
            canonical = entry_path.resolve(strict=True)
            raw = canonical.read_bytes()
        except OSError as exc:
            issues.append(self._issue("skill_read_failed", entry_path, root, exc))
            return None
        if len(raw) > self._max_skill_bytes:
            issues.append(
                SkillLoadIssue(
                    code="skill_too_large",
                    message=f"SKILL.md 超过 {self._max_skill_bytes} bytes，已忽略。",
                    path=entry_path,
                    source=root.source,
                )
            )
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(self._issue("invalid_utf8", entry_path, root, exc))
            return None
        frontmatter = self._frontmatter(text, entry_path, root, issues)
        if frontmatter is None:
            return None
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not isinstance(name, str) or not (1 <= len(name) <= 64) or not _NAME_RE.fullmatch(name):
            issues.append(
                SkillLoadIssue(
                    code="invalid_name",
                    message="name 必须是 1-64 位小写字母、数字和单连字符。",
                    path=entry_path,
                    source=root.source,
                )
            )
            return None
        if canonical.parent.name != name:
            issues.append(
                SkillLoadIssue(
                    code="name_directory_mismatch",
                    message=f"name={name!r} 与 package 目录 {canonical.parent.name!r} 不一致。",
                    path=entry_path,
                    source=root.source,
                )
            )
            return None
        if not isinstance(description, str) or not (1 <= len(description) <= 1_024):
            issues.append(
                SkillLoadIssue(
                    code="invalid_description",
                    message="description 必须是 1-1024 字符的字符串。",
                    path=entry_path,
                    source=root.source,
                )
            )
            return None
        if not self._optional_metadata_valid(frontmatter):
            issues.append(
                SkillLoadIssue(
                    code="invalid_optional_metadata",
                    message="可选 frontmatter 字段类型不符合 Agent Skills 规范。",
                    path=entry_path,
                    source=root.source,
                )
            )
            return None
        version = hashlib.sha256(raw).hexdigest()
        record_id = (
            "builtin/skill-creator"
            if root.source == "builtin" and name == "skill-creator"
            else skill_id_for_path(canonical)
        )
        return SkillRecord(
            id=record_id,
            source=root.source,
            name=name,
            description=description,
            entry_path=entry_path.absolute(),
            canonical_entry_path=canonical,
            package_root=canonical.parent,
            version=version,
            frontmatter=frontmatter,
        )

    @staticmethod
    def _frontmatter(
        text: str,
        path: Path,
        root: SkillRoot,
        issues: list[SkillLoadIssue],
    ) -> dict[str, Any] | None:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            issues.append(
                SkillLoadIssue(
                    code="missing_frontmatter",
                    message="SKILL.md 必须以 YAML frontmatter 开始。",
                    path=path,
                    source=root.source,
                )
            )
            return None
        try:
            end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration:
            issues.append(
                SkillLoadIssue(
                    code="missing_frontmatter",
                    message="SKILL.md 缺少 frontmatter 结束分隔符。",
                    path=path,
                    source=root.source,
                )
            )
            return None
        try:
            parsed = yaml.safe_load("\n".join(lines[1:end]))
        except yaml.YAMLError as exc:
            issues.append(SkillLoader._issue("invalid_yaml", path, root, exc))
            return None
        if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
            issues.append(
                SkillLoadIssue(
                    code="invalid_yaml",
                    message="frontmatter 顶层必须是字符串键对象。",
                    path=path,
                    source=root.source,
                )
            )
            return None
        return parsed

    @staticmethod
    def _optional_metadata_valid(frontmatter: dict[str, Any]) -> bool:
        for key in ("license", "compatibility", "allowed-tools"):
            value = frontmatter.get(key)
            if value is not None and not isinstance(value, str):
                return False
        metadata = frontmatter.get("metadata")
        return metadata is None or isinstance(metadata, dict)

    @staticmethod
    def _dedupe(records: list[SkillRecord], issues: list[SkillLoadIssue]) -> list[SkillRecord]:
        by_canonical: dict[str, SkillRecord] = {}
        for record in records:
            key = canonical_key(record.canonical_entry_path)
            existing = by_canonical.get(key)
            if existing is None:
                by_canonical[key] = record
                continue
            alias = SkillAlias(source=record.source, entry_path=record.entry_path)
            by_canonical[key] = replace(existing, aliases=(*existing.aliases, alias))
            issues.append(
                SkillLoadIssue(
                    code="canonical_duplicate",
                    message=f"该入口与 {existing.entry_path} 指向同一文件，已作为别名去重。",
                    path=record.entry_path,
                    source=record.source,
                )
            )
        by_name: dict[str, list[SkillRecord]] = {}
        for record in by_canonical.values():
            by_name.setdefault(record.name, []).append(record)
        for name, matches in by_name.items():
            if len(matches) < 2:
                continue
            for record in matches:
                issues.append(
                    SkillLoadIssue(
                        code="duplicate_name",
                        message=f"存在 {len(matches)} 个名为 {name!r} 的不同 Skill；请用 ID 选择。",
                        path=record.entry_path,
                        source=record.source,
                    )
                )
        return list(by_canonical.values())

    @staticmethod
    def _issue(code: str, path: Path, root: SkillRoot, exc: BaseException) -> SkillLoadIssue:
        return SkillLoadIssue(
            code=code,
            message=f"{type(exc).__name__}: {exc}",
            path=path,
            source=root.source,
        )

    @staticmethod
    def _exhaust(
        root: SkillRoot,
        path: Path,
        budget: _ScanBudget,
        issues: list[SkillLoadIssue],
    ) -> None:
        if budget.exhausted:
            return
        budget.exhausted = True
        issues.append(
            SkillLoadIssue(
                code="scan_limit_reached",
                message="Skill 扫描达到安全边界，剩余目录未处理。",
                path=path,
                source=root.source,
            )
        )
