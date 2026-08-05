"""Process-scoped Skill catalog service and per-turn activation snapshots."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import skills_dir
from .builtin import BuiltinSkillInstaller
from .catalog import render_catalog
from .loader import SkillLoader
from .models import (
    ActivatedSkill,
    SkillActivationIssue,
    SkillCatalogSnapshot,
    SkillRoot,
    SkillSelection,
    SkillTurnSnapshot,
    SkillWatcherState,
)

if TYPE_CHECKING:
    from .watcher import SkillWatcher

logger = logging.getLogger(__name__)
SnapshotSubscriber = Callable[[SkillCatalogSnapshot], None]


def default_shared_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


class SkillCatalogService:
    """Own one atomically replaceable catalog and freeze selections per user turn."""

    def __init__(
        self,
        *,
        personal_root: Path | None = None,
        shared_root: Path | None = None,
        builtin_installer: BuiltinSkillInstaller | None = None,
        loader: SkillLoader | None = None,
        watcher_enabled: bool = True,
    ) -> None:
        self.personal_root = personal_root or skills_dir()
        self.shared_root = shared_root or default_shared_skills_dir()
        self._installer = builtin_installer or BuiltinSkillInstaller(target_root=self.personal_root)
        self._loader = loader or SkillLoader()
        self._watcher_enabled = watcher_enabled
        self._lock = threading.RLock()
        self._snapshot = SkillCatalogSnapshot(generation=0, fingerprint="", entries=())
        self._watcher_state = SkillWatcherState()
        self._subscribers: list[SnapshotSubscriber] = []
        self._builtin_root: Path | None = None
        self._watcher: SkillWatcher | None = None

    @property
    def snapshot(self) -> SkillCatalogSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def watcher_state(self) -> SkillWatcherState:
        with self._lock:
            return self._watcher_state

    @property
    def watch_roots(self) -> tuple[Path, ...]:
        snapshot = self.snapshot
        roots = [self.personal_root, self.shared_root, *snapshot.external_watch_roots]
        unique: dict[str, Path] = {}
        for path in roots:
            try:
                key = str(path.resolve(strict=True)).casefold()
            except OSError:
                key = str(path.absolute()).casefold()
            unique.setdefault(key, path)
        return tuple(unique.values())

    def initialize(self) -> SkillCatalogSnapshot:
        self._builtin_root = self._installer.install()
        return self.refresh()

    def start(self) -> None:
        if self._builtin_root is None:
            self.initialize()
        if not self._watcher_enabled or self._watcher is not None:
            return
        from .watcher import SkillWatcher

        self._watcher = SkillWatcher(self)
        self._watcher.start()

    def stop(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.stop()

    def refresh(self) -> SkillCatalogSnapshot:
        if self._builtin_root is None:
            self._builtin_root = self._installer.install()
        previous = self.snapshot
        roots = (
            SkillRoot("builtin", self._builtin_root),
            SkillRoot("personal", self.personal_root),
            SkillRoot("shared", self.shared_root),
        )
        candidate = self._loader.load(roots, generation=previous.generation + 1)
        with self._lock:
            self._snapshot = candidate
            self._watcher_state = SkillWatcherState(
                status=("healthy" if self._watcher_state.status != "stopped" else "stopped"),
                last_refresh_at=datetime.now(UTC).isoformat(),
                watch_roots=self.watch_roots,
            )
            subscribers = tuple(self._subscribers)
        logger.info(
            "Skill catalog refreshed generation=%d valid=%d invalid=%d fingerprint=%s",
            candidate.generation,
            len(candidate.entries),
            len(candidate.issues),
            candidate.fingerprint,
        )
        for subscriber in subscribers:
            try:
                subscriber(candidate)
            except Exception:
                logger.warning("Skill catalog subscriber failed", exc_info=True)
        return candidate

    def subscribe(self, callback: SnapshotSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def begin_turn(
        self,
        selections: tuple[SkillSelection, ...] = (),
        *,
        model_window: int | None = None,
    ) -> SkillTurnSnapshot:
        snapshot = self.snapshot
        activations, issues, needs_refresh = self._activate(snapshot, selections)
        if needs_refresh:
            snapshot = self.refresh()
            activations, issues, _ = self._activate(snapshot, selections, final_attempt=True)
        return SkillTurnSnapshot(
            catalog=snapshot,
            rendered_catalog=render_catalog(snapshot, model_window=model_window),
            activations=activations,
            activation_issues=issues,
        )

    def catalog_payload(self) -> dict[str, object]:
        snapshot = self.snapshot
        return {
            "generation": snapshot.generation,
            "fingerprint": snapshot.fingerprint,
            "watcher": self.watcher_state.to_dict(),
            "skills": [record.to_dict() for record in snapshot.entries],
            "issues": [issue.to_dict() for issue in snapshot.issues],
        }

    def _activate(
        self,
        snapshot: SkillCatalogSnapshot,
        selections: tuple[SkillSelection, ...],
        *,
        final_attempt: bool = False,
    ) -> tuple[tuple[ActivatedSkill, ...], tuple[SkillActivationIssue, ...], bool]:
        records = snapshot.by_id()
        activations: list[ActivatedSkill] = []
        issues: list[SkillActivationIssue] = []
        seen: set[str] = set()
        needs_refresh = False
        for selection in selections:
            if selection.id in seen:
                continue
            seen.add(selection.id)
            record = records.get(selection.id)
            if record is None:
                issues.append(
                    SkillActivationIssue(
                        code="skill_not_found",
                        message="所选 Skill 当前不存在或已失效。",
                        skill_id=selection.id,
                        expected_version=selection.expected_version,
                    )
                )
                continue
            if selection.expected_version and selection.expected_version != record.version:
                issues.append(
                    SkillActivationIssue(
                        code="skill_version_mismatch",
                        message="所选 Skill 版本已变化，请刷新 catalog 后重新选择。",
                        skill_id=selection.id,
                        expected_version=selection.expected_version,
                    )
                )
                continue
            try:
                raw = record.canonical_entry_path.read_bytes()
                version = hashlib.sha256(raw).hexdigest()
                contents = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                if not final_attempt:
                    needs_refresh = True
                    continue
                issues.append(
                    SkillActivationIssue(
                        code="skill_read_failed",
                        message="所选 Skill 当前不可读。",
                        skill_id=selection.id,
                        expected_version=selection.expected_version,
                    )
                )
                continue
            if version != record.version:
                if not final_attempt:
                    needs_refresh = True
                    continue
                issues.append(
                    SkillActivationIssue(
                        code="skill_changed_during_activation",
                        message="所选 Skill 在激活期间再次变化，请重试。",
                        skill_id=selection.id,
                        expected_version=selection.expected_version,
                    )
                )
                continue
            activations.append(ActivatedSkill(record=record, contents=contents, version=version))
        return tuple(activations), tuple(issues), needs_refresh

    def _set_watcher_state(self, state: SkillWatcherState) -> None:
        with self._lock:
            self._watcher_state = state
