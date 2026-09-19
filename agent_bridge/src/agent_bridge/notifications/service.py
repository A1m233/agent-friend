"""Bounded host workers and presentation receipts; never schedules an Agent run."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.approvals import ApprovalBroker
from agent.notifications import (
    NotificationConflict,
    NotificationFact,
    NotificationRecord,
    NotificationStore,
    fallback_text,
)
from agent.personas import BUILTIN_DEFAULT_PERSONA_ID
from agent.runtime.models import RunOutcome, RunSnapshot
from agent.sessions import Session, SessionPersistError
from agent.sessions.metadata import SessionMetadataStore
from agent.sessions.store import SessionStore
from agent.todos import ReminderOccurrence, TodoStore

from agent import Event
from memory import HostEpisode, HostEpisodeConflict, Memory
from runtime_config import DesktopSettingsReader

from ..presence import PresenceRegistry
from .debug import DEBUG_EVENT_PREFIX
from .generation import NotificationGenerator

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        *,
        store: NotificationStore,
        sessions: SessionStore,
        presence: PresenceRegistry,
        reader: DesktopSettingsReader,
        generator: NotificationGenerator,
        publisher: Callable[[dict[str, Any]], None],
        memory: Memory | None = None,
        todos: TodoStore | None = None,
        approvals: ApprovalBroker | None = None,
        session_metadata_store: SessionMetadataStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store, self.sessions, self.presence = store, sessions, presence
        self.reader, self.generator, self.publisher = reader, generator, publisher
        self.memory, self.todos, self.approvals, self.clock = memory, todos, approvals, clock
        self.session_metadata_store = session_metadata_store
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._projection_lock = threading.Lock()

    def start(self) -> None:
        if self._threads:
            return
        self._stopping.clear()
        for item in self.store.recover_generation():
            self._publish(item)
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"Notification-{i}")
            for i in range(2)
        ]
        for thread in self._threads:
            thread.start()

    def quiesce(self) -> None:
        self._stopping.set()
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> bool:
        self.quiesce()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        stopped = not any(thread.is_alive() for thread in self._threads)
        if stopped:
            self._threads = []
        return stopped

    def _publish(self, item: NotificationRecord) -> NotificationRecord:
        try:
            self.publisher(item.to_wire())
        except Exception:
            logger.warning(
                "notification publication failed id=%s", item.notification_id, exc_info=True
            )
        return item

    def accept(
        self, fact: NotificationFact, *, provided_text: str | None = None
    ) -> NotificationRecord:
        if fact.kind == "run_failed":
            provided_text = fallback_text(fact)
        item = self.store.accept(fact, provided_text=provided_text)
        item = self._revalidate(item)
        self._publish(item)
        self._wake.set()
        return item

    def _settings(self) -> dict[str, Any]:
        return self.reader.read() or {}

    def _locked(self) -> bool:
        state = self.presence.snapshot(now=self.clock(), pet_available=True).desktop
        return state is not None and state.session_state in {"locked", "sleeping"}

    def _valid(self, item: NotificationRecord) -> bool:
        fact = item.fact
        settings = self._settings()
        if fact.expires_at is not None and self.clock() >= fact.expires_at:
            return False
        if fact.kind == "reply_ready":
            mode = settings.get("notificationCompletionMode", "whenNotViewing")
            if mode == "never":
                return False
            if mode == "whenNotViewing":
                desktop = self.presence.snapshot(now=self.clock(), pet_available=True).desktop
                if desktop is not None and (
                    self.clock() - desktop.observed_at <= timedelta(seconds=15)
                    and desktop.current_session_id == fact.session_id
                    and desktop.session_state == "active"
                    and desktop.window_visible
                    and desktop.window_focused
                    and not desktop.window_minimized
                ):
                    return False
        if fact.kind == "needs_action":
            if not settings.get("notificationNeedsActionEnabled", True):
                return False
            if self.approvals is None or not any(
                request.request_id == fact.approval_id for request in self.approvals.pending()
            ):
                return False
        if fact.kind in {"run_failed", "run_interrupted"} and not settings.get(
            "notificationFailureEnabled", True
        ):
            return False
        if fact.occurrence_id and self.todos is not None:
            try:
                if self.todos.get_occurrence(fact.occurrence_id).invalidated_at is not None:
                    return False
            except Exception:
                return False
        if fact.session_id:
            try:
                if not self.sessions.exists(fact.session_id):
                    return False
            except Exception:
                return False
        return True

    def _revalidate(self, item: NotificationRecord) -> NotificationRecord:
        if item.delivery["status"] in {"presented", "dismissed", "invalidated"}:
            return item
        if not self._valid(item):
            expired = item.fact.expires_at is not None and self.clock() >= item.fact.expires_at
            return self._publish(self.store.suppress(item.notification_id, expired=expired))
        return item

    def snapshot(self) -> list[dict[str, Any]]:
        # Include tombstones so a delayed push/snapshot cannot resurrect an old bubble.
        return [self._revalidate(item).to_wire() for item in self.store.list()]

    def prepare(self, notification_id: str, *, version: int, window_id: str) -> NotificationRecord:
        self._revalidate(self.store.get(notification_id))
        if self._locked() or self._stopping.is_set():
            raise NotificationConflict("desktop is not available for presentation")
        return self._publish(
            self.store.prepare(notification_id, version=version, window_id=window_id)
        )

    def presented(
        self, notification_id: str, *, attempt: int, text: str, truncated: bool
    ) -> tuple[NotificationRecord, bool]:
        self._revalidate(self.store.get(notification_id))
        if self._locked():
            raise NotificationConflict("desktop is locked or sleeping")
        result, play_sound = self.store.presented(
            notification_id,
            attempt=attempt,
            text=text,
            truncated=truncated,
            now=self.clock(),
            sound_enabled=bool(self._settings().get("notificationSoundEnabled", True)),
        )
        self._publish(result)
        self._wake.set()
        return result, play_sound

    def dismiss(
        self,
        notification_id: str,
        *,
        version: int,
        silent_replacement_ids: Sequence[str] = (),
    ) -> NotificationRecord:
        return self._publish(
            self.store.dismiss(
                notification_id,
                version=version,
                now=self.clock(),
                silent_replacement_ids=silent_replacement_ids,
            )
        )

    def run_once(self) -> bool:
        if self._stopping.is_set():
            return False
        self._project_memory()
        if self._locked():
            return False
        item = self.store.claim_next()
        if item is None:
            return False
        item = self._revalidate(item)
        if item.generation_status != "generating":
            return True
        self._publish(item)
        notification_id = item.notification_id
        try:
            if item.fact.kind == "run_failed":
                text, source, diagnostic = fallback_text(item.fact), "fallback", "local_failure"
            else:
                text = self.generator.generate(
                    item.fact,
                    stopping=self._stopping,
                    still_valid=lambda: self._generation_valid(notification_id),
                )
                source, diagnostic = "generated", ""
        except Exception as exc:
            text, source, diagnostic = fallback_text(item.fact), "fallback", type(exc).__name__
            logger.info("notification fallback id=%s reason=%s", item.notification_id, diagnostic)
        if self._stopping.is_set():
            # Leave generating durable: next startup uses fallback without another model call.
            return True
        item = self._revalidate(self.store.get(item.notification_id))
        if item.generation_status == "generating":
            self._publish(
                self.store.ready(item.notification_id, text, source=source, diagnostic=diagnostic)
            )
        return True

    def _generation_valid(self, notification_id: str) -> bool:
        item = self.store.get(notification_id)
        return (
            item.generation_status == "generating"
            and item.delivery["status"] not in {"dismissed", "invalidated"}
            and self._valid(item)
        )

    def _worker(self) -> None:
        while not self._stopping.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                logger.exception("notification worker iteration failed")
            self._wake.wait(1.0)
            self._wake.clear()

    def _project_memory(self) -> None:
        if not self._projection_lock.acquire(blocking=False):
            return
        try:
            for item in self.store.list():
                delivery = item.delivery
                if not delivery["presentedAt"] or delivery["memoryStatus"] != "pending":
                    continue
                if item.fact.event_key.startswith(DEBUG_EVENT_PREFIX):
                    self.store.mark_memory(item.notification_id, "discarded")
                    continue
                if self.memory is None:
                    continue
                try:
                    outcome = self.memory.record_host_episode(
                        HostEpisode(
                            notification_id=item.notification_id,
                            persona_id=item.fact.persona_id,
                            owner_user_id=item.fact.owner_user_id,
                            occurred_at=datetime.fromisoformat(delivery["presentedAt"]),
                            summary=delivery["memorySummary"],
                        )
                    )
                    self.store.mark_memory(
                        item.notification_id, "discarded" if outcome == "deleted" else "projected"
                    )
                except HostEpisodeConflict:
                    self.store.mark_memory(item.notification_id, "conflict")
                    logger.exception("notification episode conflict id=%s", item.notification_id)
                except Exception:
                    logger.warning(
                        "notification episode projection failed id=%s",
                        item.notification_id,
                        exc_info=True,
                    )
        finally:
            self._projection_lock.release()

    def _session_fact(
        self,
        session: Session,
        *,
        event_key: str,
        kind: Any,
        run_id: str | None = None,
        approval_id: str | None = None,
    ) -> NotificationFact:
        title = session.initial_title
        if self.session_metadata_store is not None:
            try:
                title = self.session_metadata_store.get(session.session_id).display_title or title
            except (SessionPersistError, UnicodeError):
                logger.warning(
                    "notification session title read failed session=%s; using initial title",
                    session.session_id,
                    exc_info=True,
                )
        events = session.active_events
        task = next(
            (
                str(e.payload.get("content", ""))
                for e in reversed(events)
                if e.type == "user_message"
            ),
            "",
        )
        reply = next(
            (
                str(e.payload.get("content", ""))
                for e in reversed(events)
                if e.type == "assistant_message"
                and (run_id is None or e.meta.get("run_id") == run_id)
            ),
            "",
        )
        return NotificationFact(
            event_key=event_key,
            kind=kind,
            title=title[:120],
            persona_id=session.current_persona_id or BUILTIN_DEFAULT_PERSONA_ID,
            session_id=session.session_id,
            run_id=run_id,
            approval_id=approval_id,
            task=task[:300],
            reply=reply[:800],
            created_at=self.clock(),
        )

    def on_terminal(self, snapshot: RunSnapshot) -> None:
        if (
            self._stopping.is_set()
            or snapshot.source_kind == "todo_reminder"
            or snapshot.output_visibility == "memory_only"
        ):
            return
        if snapshot.presentation == "none":
            return
        try:
            session = self.sessions.load(snapshot.session_id)
        except Exception:
            return  # Stateless/private editor runs have no persistent destination.
        if snapshot.outcome is RunOutcome.COMPLETED:
            kind = "reply_ready"
        elif snapshot.outcome is RunOutcome.FAILED:
            kind = "run_failed"
        elif snapshot.outcome is RunOutcome.CANCELLED and snapshot.reason_code in {
            "client_disconnected",
            "consumer_abandoned",
            "computer_or_owner_runtime_lost",
        }:
            kind = "run_interrupted"
        else:
            return
        fact = self._session_fact(
            session,
            event_key=f"run:{snapshot.session_id}:{snapshot.run_id}",
            kind=kind,
            run_id=snapshot.run_id,
        )
        if kind == "reply_ready" and not fact.reply:
            return
        if snapshot.presentation == "ambient":
            # Existing ambient messages already have wording; do not call another model.
            if fact.reply:
                self.accept(replace(fact, kind="ambient"), provided_text=fact.reply[:160])
            return
        self.accept(fact)

    def on_recovered(self, session: Session, terminal: Event) -> None:
        if self._stopping.is_set():
            return
        run_id = str(terminal.payload["run_id"])
        start = next(
            (
                event
                for event in session.active_events
                if event.type == "run_started" and event.payload.get("run_id") == run_id
            ),
            None,
        )
        if start is not None and (
            start.payload.get("source_kind") == "todo_reminder"
            or start.payload.get("output_visibility") == "memory_only"
            or start.payload.get("presentation") == "none"
        ):
            return
        self.accept(
            self._session_fact(
                session,
                event_key=f"run:{session.session_id}:{run_id}",
                kind="run_interrupted",
                run_id=run_id,
            )
        )

    def on_approval(self, payload: dict[str, object]) -> None:
        request_id = str(payload.get("request_id") or "")
        if payload.get("status") != "pending":
            for item in self.store.list():
                if item.fact.approval_id == request_id:
                    self._revalidate(item)
            return
        if self._stopping.is_set():
            return
        try:
            session = self.sessions.load(str(payload.get("session_id") or ""))
        except Exception:
            return
        self.accept(
            self._session_fact(
                session,
                event_key=f"approval:{request_id}",
                kind="needs_action",
                run_id=str(payload.get("run_id") or ""),
                approval_id=request_id,
            )
        )

    def on_todo(self, occurrence: ReminderOccurrence) -> str:
        if occurrence.persistence_session_id is None:
            raise ValueError("reminder persistence target is not frozen")
        session = self.sessions.load(occurrence.persistence_session_id)
        fact = NotificationFact(
            event_key=f"todo:{occurrence.id}",
            kind="todo_due",
            title=occurrence.facts.title[:120],
            persona_id=session.current_persona_id or BUILTIN_DEFAULT_PERSONA_ID,
            session_id=session.session_id,
            occurrence_id=occurrence.id,
            detail=occurrence.facts.description[:600],
            created_at=self.clock(),
        )
        return self.accept(fact).notification_id
