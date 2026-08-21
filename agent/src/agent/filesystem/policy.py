"""Protected product paths and standard user-facing filesystem locators."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..paths import log_dir, skills_dir, user_data_dir


def canonical_path(path: Path, *, allow_missing: bool = True) -> Path:
    expanded = path.expanduser()
    if expanded.exists() or not allow_missing:
        return expanded.resolve(strict=not allow_missing)
    missing: list[str] = []
    current = expanded
    while not current.exists():
        if current.parent == current:
            raise FileNotFoundError(str(path))
        missing.append(current.name)
        current = current.parent
    resolved = current.resolve(strict=True)
    for component in reversed(missing):
        resolved = resolved / component
    return resolved


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class ProtectedPathPolicy:
    product_roots: tuple[Path, ...]
    personal_skill_roots: tuple[Path, ...]
    builtin_skill_roots: tuple[Path, ...] = ()

    @classmethod
    def default(cls, *, shared_skill_root: Path | None = None) -> ProtectedPathPolicy:
        shared = shared_skill_root or Path.home() / ".agents" / "skills"
        builtin_env = os.environ.get("AGENT_FRIEND_BUILTIN_SKILLS_DIR")
        builtin = [canonical_path(skills_dir() / ".system")]
        if builtin_env:
            builtin.append(canonical_path(Path(builtin_env)))
        return cls(
            product_roots=(canonical_path(user_data_dir()), canonical_path(log_dir())),
            personal_skill_roots=(canonical_path(skills_dir()), canonical_path(shared)),
            builtin_skill_roots=tuple(builtin),
        )

    def is_protected(self, path: Path) -> bool:
        return self.is_instruction_metadata(path) or any(
            is_within(path, root) for root in self.product_roots + self.builtin_skill_roots
        )

    def explicit_read_allowed(self, requested_root: Path) -> bool:
        return self.is_instruction_metadata(requested_root) or any(
            is_within(requested_root, root)
            for root in self.product_roots + self.builtin_skill_roots
        )

    def generic_write_allowed(
        self,
        path: Path,
        *,
        allow_instruction_metadata: bool = False,
    ) -> bool:
        if any(is_within(path, root) for root in self.builtin_skill_roots):
            return False
        if any(is_within(path, root) for root in self.personal_skill_roots):
            return True
        if any(is_within(path, root) for root in self.product_roots):
            return False
        if self.is_instruction_metadata(path):
            return allow_instruction_metadata
        return True

    @staticmethod
    def is_instruction_metadata(path: Path) -> bool:
        protected_directories = {".git", ".cursor", ".codex", ".claude"}
        protected_files = {"agents.md", "claude.md", ".cursorrules"}
        return any(part.casefold() in protected_directories for part in path.parts) or (
            path.name.casefold() in protected_files
        )


def resolve_locator(value: object) -> Path:
    if not isinstance(value, dict):
        raise ValueError("locator 必须是对象")
    kind = value.get("kind")
    if kind == "path":
        raw = value.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("path locator 需要非空 path")
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError("path locator 必须是绝对路径")
        return canonical_path(path)
    if kind != "standard":
        raise ValueError("locator.kind 必须是 path 或 standard")
    name = value.get("name")
    home = Path.home()
    locations = {
        "home": home,
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "agent_data": user_data_dir(),
        "agent_logs": log_dir(),
        "personal_skills": skills_dir(),
        "shared_skills": home / ".agents" / "skills",
    }
    if name not in locations:
        raise ValueError("不支持的标准位置")
    return canonical_path(locations[str(name)])
