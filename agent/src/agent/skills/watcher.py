"""Lifecycle-owned filesystem watcher for live Skill catalog refreshes."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Collection
from pathlib import Path

from watchfiles import watch

from .models import SkillWatcherState
from .service import SkillCatalogService

logger = logging.getLogger(__name__)


class SkillWatcher:
    """Run watchfiles in one daemon thread and always rebuild complete snapshots."""

    def __init__(self, service: SkillCatalogService) -> None:
        self._service = service
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="agent-friend-skill-watcher",
            daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(timeout=2)

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None
        current = self._service.watcher_state
        self._service._set_watcher_state(
            SkillWatcherState(
                status="stopped",
                last_refresh_at=current.last_refresh_at,
                watch_roots=current.watch_roots,
            )
        )

    def _run(self) -> None:
        backoff = 1
        last_reconcile = time.monotonic()
        while not self._stop_event.is_set():
            roots = self._existing_watch_roots()
            self._service._set_watcher_state(
                SkillWatcherState(
                    status="healthy",
                    last_refresh_at=self._service.watcher_state.last_refresh_at,
                    watch_roots=roots,
                )
            )
            try:
                restart = False
                for changes in watch(
                    *roots,
                    stop_event=self._stop_event,
                    debounce=500,
                    step=100,
                    recursive=True,
                    rust_timeout=1_000,
                    yield_on_timeout=True,
                ):
                    self._ready_event.set()
                    if self._stop_event.is_set():
                        return
                    desired = self._existing_watch_roots()
                    if desired != roots:
                        self._service.refresh()
                        restart = True
                    if changes and self._relevant(changes):
                        self._service.refresh()
                        last_reconcile = time.monotonic()
                        desired = self._existing_watch_roots()
                        restart = restart or desired != roots
                    elif time.monotonic() - last_reconcile >= 60:
                        self._service.refresh()
                        last_reconcile = time.monotonic()
                    if restart:
                        break
                backoff = 1
            except Exception as exc:
                self._ready_event.set()
                logger.warning("Skill watcher degraded", exc_info=True)
                current = self._service.watcher_state
                self._service._set_watcher_state(
                    SkillWatcherState(
                        status="degraded",
                        last_refresh_at=current.last_refresh_at,
                        error=f"{type(exc).__name__}: {exc}",
                        watch_roots=roots,
                    )
                )
                if self._stop_event.wait(backoff):
                    return
                backoff = min(30, backoff * 2)

    def _existing_watch_roots(self) -> tuple[Path, ...]:
        roots = tuple(path for path in self._service.watch_roots if path.exists())
        if roots:
            return roots
        # initialize() creates personal_root, but keep a defensive parent fallback.
        return (self._service.personal_root.parent,)

    def _relevant(self, changes: Collection[tuple[object, str]]) -> bool:
        personal_system = self._service.personal_root / ".system"
        for _, raw_path in changes:
            path = Path(raw_path)
            try:
                path.relative_to(personal_system)
            except ValueError:
                return True
        return False
