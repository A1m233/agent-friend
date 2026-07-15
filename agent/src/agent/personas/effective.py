"""默认 persona 的用户覆盖、回退与乐观并发控制。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..errors import (
    PersonaContentInvalidError,
    PersonaPersistError,
    PersonaRevisionConflictError,
    PersonaUndoUnavailableError,
)
from ..paths import editable_persona_path, persona_internal_dir
from .catalog import PersonaCatalog
from .identity import BUILTIN_DEFAULT_PERSONA_ID

MAX_PERSONA_CHARS = 32_000
PERSONA_DISPLAY_NAME_FALLBACK = "你的朋友"
MAX_PERSONA_DISPLAY_NAME_CHARS = 80

PersonaSource = Literal["builtin", "user", "last_valid"]
PersonaSourceStatus = Literal["builtin", "valid", "invalid", "missing"]


@dataclass(frozen=True)
class PersonaDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class EffectivePersonaSnapshot:
    content: str
    display_name: str
    source: PersonaSource
    source_status: PersonaSourceStatus
    revision: str
    diagnostic: PersonaDiagnostic | None = None
    editable_file_exists: bool = False
    can_undo_apply: bool = False


def normalize_persona_content(content: str) -> str:
    """统一换行并校验公开 ``persona.md`` 正文。"""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise PersonaContentInvalidError("人格正文不能为空")
    if len(normalized) > MAX_PERSONA_CHARS:
        raise PersonaContentInvalidError(f"人格正文不能超过 {MAX_PERSONA_CHARS} 个字符")
    if normalized.startswith("---\n"):
        raise PersonaContentInvalidError("persona.md 只包含正文，不能包含 YAML frontmatter")
    return normalized + "\n"


def extract_persona_display_name(content: str) -> str:
    """把 persona.md 第一处一级标题投影为展示名；正文有效性不依赖标题存在。"""
    fence: str | None = None
    for line in content.splitlines():
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_match is not None:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"^ {0,3}#(?!#)\s+(.+?)\s*$", line)
        if match is None:
            continue
        heading = re.sub(r"\s+#+\s*$", "", match.group(1))
        heading = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", heading)
        heading = re.sub(r"[*_~`]", "", heading)
        heading = re.sub(r"<[^>]+>", "", heading)
        heading = " ".join(heading.split()).strip()
        if heading:
            return heading[:MAX_PERSONA_DISPLAY_NAME_CHARS].rstrip()
    return PERSONA_DISPLAY_NAME_FALLBACK


class EffectivePersonaStore:
    """管理默认 persona 的内置基线与唯一用户覆盖。"""

    def __init__(
        self,
        *,
        user_file: Path | None = None,
        internal_dir: Path | None = None,
        builtin_loader: Callable[[], str] | None = None,
    ) -> None:
        self.user_file = user_file or editable_persona_path()
        self.internal_dir = internal_dir or persona_internal_dir()
        self._last_valid_file = self.internal_dir / "last-valid.md"
        self._state_file = self.internal_dir / "state.json"
        self._undo_file = self.internal_dir / "undo.md"
        self._builtin_loader = builtin_loader or self._load_builtin_default
        self._lock = threading.RLock()

    @staticmethod
    def _load_builtin_default() -> str:
        return PersonaCatalog().read_content(BUILTIN_DEFAULT_PERSONA_ID)

    def load(self) -> EffectivePersonaSnapshot:
        with self._lock:
            return self._load_locked(update_cache=True)

    def materialize(self) -> EffectivePersonaSnapshot:
        with self._lock:
            return self._load_locked(update_cache=True)

    def apply(self, content: str, *, expected_revision: str) -> EffectivePersonaSnapshot:
        normalized = normalize_persona_content(content)
        with self._lock:
            current = self._load_locked(update_cache=True)
            if current.revision != expected_revision:
                raise PersonaRevisionConflictError(
                    "persona.md 已在编辑期间发生变化，请基于最新版本重新调整"
                )
            self._atomic_write(self._undo_file, normalize_persona_content(current.content))
            self._atomic_write(self.user_file, normalized)
            return self._load_locked(update_cache=True)

    def undo_apply(self) -> EffectivePersonaSnapshot:
        with self._lock:
            if not self._undo_file.exists():
                raise PersonaUndoUnavailableError("没有可撤销的引导式人格修改")
            try:
                content = normalize_persona_content(self._undo_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, PersonaContentInvalidError) as exc:
                raise PersonaPersistError(f"读取人格撤销点失败: {exc}") from exc
            self._atomic_write(self.user_file, content)
            try:
                self._undo_file.unlink()
            except OSError as exc:
                raise PersonaPersistError(f"消费人格撤销点失败: {exc}") from exc
            return self._load_locked(update_cache=True)

    def reset(self) -> EffectivePersonaSnapshot:
        with self._lock:
            builtin = normalize_persona_content(self._builtin_loader())
            self._atomic_write(self.user_file, builtin)
            try:
                self._undo_file.unlink(missing_ok=True)
            except OSError as exc:
                raise PersonaPersistError(f"删除人格撤销点失败: {self._undo_file}: {exc}") from exc
            return self._load_locked(update_cache=True)

    def _load_locked(self, *, update_cache: bool) -> EffectivePersonaSnapshot:
        builtin = normalize_persona_content(self._builtin_loader())
        can_undo = self._undo_file.exists()
        if not self.user_file.exists():
            try:
                self._atomic_write(self.user_file, builtin)
            except PersonaPersistError as exc:
                return EffectivePersonaSnapshot(
                    content=builtin.rstrip(),
                    display_name=extract_persona_display_name(builtin),
                    source="builtin",
                    source_status="missing",
                    revision=self._revision("missing", b""),
                    diagnostic=PersonaDiagnostic(
                        "persona_write_failed",
                        f"无法创建 persona.md，请检查目录权限后重试：{exc}",
                    ),
                    editable_file_exists=False,
                    can_undo_apply=can_undo,
                )

        try:
            raw = self.user_file.read_bytes()
        except OSError as exc:
            return self._fallback_invalid(
                builtin,
                revision=self._revision("unreadable", str(exc).encode("utf-8")),
                diagnostic=PersonaDiagnostic(
                    "persona_read_failed",
                    "无法读取 persona.md，请检查文件权限后重试",
                ),
                can_undo=can_undo,
            )

        revision = self._revision("present", raw)
        try:
            decoded = raw.decode("utf-8")
            normalized = normalize_persona_content(decoded)
        except (UnicodeError, PersonaContentInvalidError) as exc:
            return self._fallback_invalid(
                builtin,
                revision=revision,
                diagnostic=PersonaDiagnostic("persona_invalid_content", str(exc)),
                can_undo=can_undo,
            )

        if update_cache:
            try:
                self._atomic_write(self._last_valid_file, normalized)
                self._write_state(revision=revision, source_status="valid")
            except PersonaPersistError:
                # 当前有效正文已经成功读取；内部容灾副本失败不应阻断本回合。
                pass
        return EffectivePersonaSnapshot(
            content=normalized.rstrip(),
            display_name=extract_persona_display_name(normalized),
            source="user",
            source_status="valid",
            revision=revision,
            editable_file_exists=True,
            can_undo_apply=can_undo,
        )

    def _fallback_invalid(
        self,
        builtin: str,
        *,
        revision: str,
        diagnostic: PersonaDiagnostic,
        can_undo: bool,
    ) -> EffectivePersonaSnapshot:
        try:
            cached = normalize_persona_content(self._last_valid_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, PersonaContentInvalidError):
            cached = builtin
            source: PersonaSource = "builtin"
        else:
            source = "last_valid"
        return EffectivePersonaSnapshot(
            content=cached.rstrip(),
            display_name=extract_persona_display_name(cached),
            source=source,
            source_status="invalid",
            revision=revision,
            diagnostic=diagnostic,
            editable_file_exists=True,
            can_undo_apply=can_undo,
        )

    @staticmethod
    def _revision(state: str, payload: bytes) -> str:
        digest = hashlib.sha256(state.encode("utf-8") + b"\0" + payload).hexdigest()
        return f"sha256:{digest}"

    def _write_state(self, *, revision: str, source_status: str) -> None:
        payload = json.dumps(
            {"revision": revision, "sourceStatus": source_status},
            ensure_ascii=False,
            indent=2,
        )
        self._atomic_write(self._state_file, payload + "\n")

    @staticmethod
    def _atomic_write(target: Path, text: str) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, target)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise PersonaPersistError(f"原子写人格文件失败: {target}: {exc}") from exc


class EffectivePersonaCatalog:
    """仅覆盖默认 ID 正文，其余 catalog 行为继续委托旧实现。"""

    def __init__(self, store: EffectivePersonaStore, fallback: PersonaCatalog) -> None:
        self._store = store
        self._fallback = fallback

    def read_content(self, persona_id: str) -> str:
        if persona_id == BUILTIN_DEFAULT_PERSONA_ID:
            return self._store.load().content
        return self._fallback.read_content(persona_id)


__all__ = [
    "MAX_PERSONA_CHARS",
    "MAX_PERSONA_DISPLAY_NAME_CHARS",
    "PERSONA_DISPLAY_NAME_FALLBACK",
    "EffectivePersonaCatalog",
    "EffectivePersonaSnapshot",
    "EffectivePersonaStore",
    "PersonaDiagnostic",
    "extract_persona_display_name",
    "normalize_persona_content",
]
