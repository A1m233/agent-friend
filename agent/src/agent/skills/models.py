"""Immutable domain models shared by Skill discovery, activation, and protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .package_snapshot import SkillPackageSnapshot

SkillSource = Literal["builtin", "personal", "shared"]
SkillInvocationKind = Literal["caller", "implicit"]


@dataclass(frozen=True)
class SkillRoot:
    """One logical discovery root and its stable source classification."""

    source: SkillSource
    path: Path


@dataclass(frozen=True)
class SkillAlias:
    """An additional logical path resolving to the same canonical SKILL.md."""

    source: SkillSource
    entry_path: Path


@dataclass(frozen=True)
class SkillLoadIssue:
    """A stable, serializable discovery diagnostic that does not abort a scan."""

    code: str
    message: str
    path: Path | None = None
    source: SkillSource | None = None

    def to_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.path is not None:
            result["path"] = str(self.path)
        if self.source is not None:
            result["source"] = self.source
        return result


@dataclass(frozen=True)
class SkillActivationIssue:
    """A per-turn failure to resolve or freeze one structured selection."""

    code: str
    message: str
    skill_id: str
    expected_version: str | None = None


@dataclass(frozen=True)
class SkillRecord:
    """Validated metadata for one canonical Skill entry file."""

    id: str
    source: SkillSource
    name: str
    description: str
    entry_path: Path
    canonical_entry_path: Path
    package_root: Path
    version: str
    frontmatter: Mapping[str, object]
    allow_implicit_invocation: bool = True
    aliases: tuple[SkillAlias, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "frontmatter", MappingProxyType(dict(self.frontmatter)))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "version": self.version,
            "allow_implicit_invocation": self.allow_implicit_invocation,
            "path": str(self.entry_path),
            "aliases": [
                {"source": alias.source, "path": str(alias.entry_path)} for alias in self.aliases
            ],
        }


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    """Atomically published, immutable view of all valid Skills."""

    generation: int
    fingerprint: str
    entries: tuple[SkillRecord, ...]
    issues: tuple[SkillLoadIssue, ...] = ()
    external_watch_roots: tuple[Path, ...] = ()

    def by_id(self) -> dict[str, SkillRecord]:
        return {record.id: record for record in self.entries}


@dataclass(frozen=True)
class SkillSelection:
    """Structured caller intent; names and paths are deliberately unsupported."""

    id: str
    expected_version: str | None = None


@dataclass(frozen=True)
class ActivatedSkill:
    """One successful activation; a later activation may replace the current version."""

    record: SkillRecord
    contents: str
    version: str
    package: SkillPackageSnapshot | None = field(default=None, repr=False, compare=False)
    invocation_kind: SkillInvocationKind = "implicit"

    @property
    def resource_root(self) -> Path:
        return self.package.root if self.package is not None else self.record.package_root

    @property
    def package_version(self) -> str:
        return self.package.fingerprint if self.package is not None else self.version


@dataclass(frozen=True)
class RenderedSkillCatalog:
    """A model-visible catalog rendered for one model budget profile."""

    content: str
    fingerprint: str
    budget_profile: str
    omitted_count: int = 0
    descriptions_truncated: bool = False


@dataclass(frozen=True)
class SkillTurnSnapshot:
    """Frozen catalog plus host-recorded activations for the current user turn.

    The catalog fixes candidate identities before the first model assembly. Each activation
    binds current contents; successful rereads and verified updates replace that identity's
    active version without rewriting the model's earlier discovery messages.
    """

    catalog: SkillCatalogSnapshot
    rendered_catalog: RenderedSkillCatalog
    activations: tuple[ActivatedSkill, ...] = ()
    activation_issues: tuple[SkillActivationIssue, ...] = ()


@dataclass(frozen=True)
class SkillWatcherState:
    """Operational watcher status exposed by the catalog route."""

    status: Literal["stopped", "healthy", "degraded"] = "stopped"
    last_refresh_at: str | None = None
    error: str | None = None
    watch_roots: tuple[Path, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "last_refresh_at": self.last_refresh_at,
            "error": self.error,
            "watch_roots": [str(path) for path in self.watch_roots],
        }
