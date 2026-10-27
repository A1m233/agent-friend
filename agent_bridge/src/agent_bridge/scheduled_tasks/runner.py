"""Bridge orchestration from claimed Scheduled occurrences to AgentRuntime runs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from agent.runtime import (
    AgentRuntime,
    RunHandle,
    RunOutcome,
    RunStatus,
)
from agent.scheduled_tasks import (
    ScheduledOccurrenceClaim,
    ScheduledOccurrenceOutcome,
    ScheduledOccurrenceState,
    ScheduledTaskError,
    ScheduledTaskStore,
)
from agent.session_requests.errors import SessionRequestError
from agent.skills import SkillCatalogService, SkillSelection, SkillTurnSnapshot

from agent import SessionManager

from ..session_requests import SessionRequestService
from .notifications import ScheduledIMNotifications

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class ScheduledTaskRunOrchestrator:
    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        sessions: SessionManager,
        agent_runtime: AgentRuntime,
        tracker: ScheduledRunTracker,
        skill_service: SkillCatalogService | None,
        clock: Clock | None = None,
        request_service: SessionRequestService | None = None,
    ) -> None:
        self._request_service = request_service
        self._store = store
        self._sessions = sessions
        self._agent_runtime = agent_runtime
        self._tracker = tracker
        self._skill_service = skill_service
        self._clock = clock or (lambda: datetime.now(UTC))

    def dispatch_batch(self, claims: tuple[ScheduledOccurrenceClaim, ...]) -> None:
        for claim in claims:
            self._dispatch_one(claim)

    def _dispatch_one(self, claim: ScheduledOccurrenceClaim) -> None:
        occurrence = claim.occurrence
        if occurrence.state is not ScheduledOccurrenceState.CLAIMED:
            return
        if not occurrence.session_id or not occurrence.run_id:
            self._fail_pre_submit(
                occurrence.id,
                "missing_run_identity",
            )
            return

        selection: SkillSelection | None = None
        skill_snapshot: SkillTurnSnapshot | None = None
        skill_source: str | None = None
        skill_version: str | None = None
        skill_id = occurrence.task_snapshot.skill_id
        if skill_id is not None:
            if self._skill_service is None:
                self._fail_pre_submit(occurrence.id, "skill_service_unavailable")
                return
            initial_selection = SkillSelection(id=skill_id)
            skill_snapshot = self._skill_service.begin_turn((initial_selection,))
            if skill_snapshot.activation_issues or len(skill_snapshot.activations) != 1:
                code = (
                    skill_snapshot.activation_issues[0].code
                    if skill_snapshot.activation_issues
                    else "skill_activation_failed"
                )
                self._fail_pre_submit(occurrence.id, code)
                return
            activation = skill_snapshot.activations[0]
            selection = SkillSelection(id=activation.record.id, expected_version=activation.version)
            skill_source = activation.record.source
            skill_version = activation.version

        if self._request_service is None:
            self._fail_pre_submit(occurrence.id, "request_service_unavailable")
            return
        try:
            self._request_service.validate_target(occurrence.session_id)
        except SessionRequestError as exc:
            self._store.pause_for_target(occurrence.session_id, exc.kind)
            self._fail_pre_submit(occurrence.id, exc.kind)
            return

        try:
            self._store.mark_submitted(
                occurrence.id,
                now=self._now(),
                skill_source=skill_source,
                skill_version=skill_version,
            )

            from ..session_bridge import PersistentBootstrap

            snapshot = occurrence.task_snapshot
            handle = self._request_service.submit(
                boot=PersistentBootstrap(
                    thread_id=occurrence.session_id,
                    new_user_input=snapshot.instruction,
                    default_persona=snapshot.persona_name,
                    default_model=snapshot.model_id,
                ),
                run_id=occurrence.run_id,
                user_event_id=f"scheduled:{occurrence.id}",
                options=snapshot.request_options,
                source={
                    "kind": "scheduled_task",
                    "task_id": occurrence.task_id,
                    "occurrence_id": occurrence.id,
                    "task_name": snapshot.name,
                    "trigger_kind": occurrence.trigger_kind.value,
                    "scheduled_at_utc": occurrence.scheduled_at_utc.isoformat(),
                },
                skills=((selection,) if selection else ()),
                skill_snapshot=skill_snapshot,
                supersede_background=False,
                detached=True,
            )
        except Exception:
            logger.exception("scheduled AgentRuntime submit failed: %s", occurrence.id)
            self._store.mark_terminal(
                occurrence.id,
                outcome=ScheduledOccurrenceOutcome.REJECTED,
                reason_code="runtime_submit_failed",
                now=self._now(),
                expected=(
                    ScheduledOccurrenceState.CLAIMED,
                    ScheduledOccurrenceState.SUBMITTED,
                ),
            )
            return
        self._tracker.track(occurrence.id, handle)

    def _fail_pre_submit(self, occurrence_id: str, reason_code: str) -> None:
        try:
            self._store.mark_terminal(
                occurrence_id,
                outcome=ScheduledOccurrenceOutcome.FAILED,
                reason_code=reason_code,
                now=self._now(),
                expected=(ScheduledOccurrenceState.CLAIMED,),
            )
        except ScheduledTaskError:
            logger.exception("failed to close scheduled pre-submit failure: %s", occurrence_id)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("ScheduledTaskRunOrchestrator clock must be timezone-aware")
        return value.astimezone(UTC)


class ScheduledRunTracker:
    """One bounded polling thread persists RunHandle lifecycle snapshots."""

    def __init__(
        self,
        store: ScheduledTaskStore,
        *,
        clock: Clock | None = None,
        poll_seconds: float = 0.2,
        terminal_lookup: Callable[[str], tuple[str, str] | None] | None = None,
        notifications: ScheduledIMNotifications | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._poll_seconds = poll_seconds
        self._terminal_lookup = terminal_lookup
        self.notifications = notifications
        self._condition = threading.Condition()
        self._handles: dict[str, RunHandle] = {}
        self._stopping = False
        self._thread: threading.Thread | None = None

    def start(self) -> dict[str, int]:
        with self._condition:
            if self._thread is not None:
                return {"released_claims": 0, "interrupted_runs": 0}
            recovered = self._store.recover_startup(self._now(), self._terminal_lookup)
            if self.notifications is not None:
                self.notifications.start()
            self._handles.clear()
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="ScheduledRunTracker",
            )
            self._thread.start()
        return recovered

    def stop(self, timeout: float = 5.0) -> bool:
        notifications_stopped = self.notifications is None or self.notifications.stop(timeout)
        with self._condition:
            thread = self._thread
            if thread is None:
                return notifications_stopped
            self._stopping = True
            self._condition.notify_all()
        thread.join(timeout=timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._condition:
                tracked = tuple(self._handles.items())
                self._thread = None
            for occurrence_id, handle in tracked:
                self._persist_snapshot(occurrence_id, handle)
            if self.notifications is not None:
                self.notifications.wake()
            with self._condition:
                self._handles.clear()
        return stopped and notifications_stopped

    def track(self, occurrence_id: str, handle: RunHandle) -> None:
        with self._condition:
            self._handles[occurrence_id] = handle
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                tracked = tuple(self._handles.items())
            for occurrence_id, handle in tracked:
                self._persist_snapshot(occurrence_id, handle)
            with self._condition:
                if self._stopping:
                    return
                self._condition.wait(timeout=self._poll_seconds)

    def _persist_snapshot(self, occurrence_id: str, handle: RunHandle) -> None:
        snapshot = handle.snapshot()
        try:
            if snapshot.status is RunStatus.RUNNING:
                self._store.mark_running(occurrence_id, now=snapshot.started_at or self._now())
                return
            if snapshot.status is not RunStatus.TERMINAL or snapshot.outcome is None:
                return
            self._store.mark_terminal(
                occurrence_id,
                outcome=ScheduledOccurrenceOutcome.INTERRUPTED
                if snapshot.reason_code == "runtime_stopping"
                else _map_outcome(snapshot.outcome),
                reason_code=snapshot.reason_code,
                now=snapshot.finished_at or self._now(),
                started_at=snapshot.started_at,
                expected=(
                    ScheduledOccurrenceState.SUBMITTED,
                    ScheduledOccurrenceState.RUNNING,
                ),
            )
            if self.notifications is not None:
                self.notifications.wake()
        except ScheduledTaskError:
            logger.exception("failed to persist scheduled run snapshot: %s", occurrence_id)
        finally:
            if snapshot.status is RunStatus.TERMINAL:
                with self._condition:
                    self._handles.pop(occurrence_id, None)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("ScheduledRunTracker clock must be timezone-aware")
        return value.astimezone(UTC)


def _map_outcome(outcome: RunOutcome) -> ScheduledOccurrenceOutcome:
    mapping = {
        RunOutcome.COMPLETED: ScheduledOccurrenceOutcome.COMPLETED,
        RunOutcome.FAILED: ScheduledOccurrenceOutcome.FAILED,
        RunOutcome.CANCELLED: ScheduledOccurrenceOutcome.CANCELLED,
        RunOutcome.SKIPPED: ScheduledOccurrenceOutcome.SKIPPED,
        RunOutcome.EXPIRED: ScheduledOccurrenceOutcome.EXPIRED,
        RunOutcome.REJECTED: ScheduledOccurrenceOutcome.REJECTED,
    }
    return mapping[outcome]
