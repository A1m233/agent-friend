"""Reminder occurrence persistence, Agent response, and notification projections."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agent.conversation_events import TextDelta
from agent.runtime import AgentRuntime, RunOutcome, SystemTriggerEvent
from agent.sessions.store import SessionStore
from agent.system_prompt import DESKTOP_CHAT_SURFACE, InteractionSurfaceId
from agent.todos import (
    AgentResponseStatus,
    DeliveryUpdate,
    OccurrenceState,
    ReminderDeliveryState,
    ReminderNotificationMode,
    ReminderOccurrence,
    ReminderProjectionKind,
    TodoError,
    TodoStore,
)

from agent import Event, SessionNotFoundError

from .presence import DestinationPolicy, IMPresenceSnapshot, PresenceRegistry, PresenceState

if TYPE_CHECKING:
    from agent.runtime.models import RunHandle

    from .protocols.im.provider import OutboundSendResult
    from .protocols.im.runtime import IMRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PersistenceTarget:
    session_id: str
    destination_id: str | None
    interaction_surface: InteractionSurfaceId
    reason: str


@dataclass(frozen=True)
class _AgentResponseTarget:
    session_id: str
    destination_id: str | None
    interaction_surface: InteractionSurfaceId


class ReminderRoutingError(RuntimeError):
    pass


class ReminderDeliveryOrchestrator:
    def __init__(
        self,
        *,
        todo_store: TodoStore,
        session_store: SessionStore,
        agent_runtime: AgentRuntime,
        presence_registry: PresenceRegistry,
        im_runtime: IMRuntime | None = None,
        policy: DestinationPolicy | None = None,
        notification_mode_provider: Callable[[], ReminderNotificationMode] | None = None,
        clock: Callable[[], datetime] | None = None,
        pet_notification: Callable[[ReminderOccurrence], str] | None = None,
    ) -> None:
        self._todos = todo_store
        self._sessions = session_store
        self._agent_runtime = agent_runtime
        self._presence = presence_registry
        self._im_runtime = im_runtime
        self._policy = policy or DestinationPolicy()
        self._notification_mode_provider = notification_mode_provider or (
            lambda: ReminderNotificationMode.SMART
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pet_notification = pet_notification

    def deliver(self, occurrence: ReminderOccurrence) -> None:
        self.deliver_batch((occurrence,))

    def deliver_batch(self, occurrences: tuple[ReminderOccurrence, ...]) -> None:
        if not occurrences:
            return
        now = self._now()
        if self._im_runtime is not None:
            self._presence.replace_im(self._im_runtime.presence_snapshots())
        state = self._presence.snapshot(
            now=now,
            pet_available=self._agent_runtime.listeners.subscriber_count() > 0,
        )
        batch_id = _batch_id(tuple(occurrence.id for occurrence in occurrences))
        response_groups: dict[_AgentResponseTarget, list[ReminderOccurrence]] = {}
        for occurrence in occurrences:
            try:
                prepared = self._prepare_occurrence(occurrence, state=state, now=now)
            except Exception:
                logger.exception("Reminder occurrence preparation failed: %s", occurrence.id)
                self._mark_delivery_failed(occurrence, now=now)
                continue
            if prepared is not None:
                target, current = prepared
                response_groups.setdefault(target, []).append(current)

        for target, grouped in response_groups.items():
            try:
                self._start_agent_response(tuple(grouped), target=target, batch_id=batch_id)
            except Exception:
                logger.exception(
                    "Failed to start reminder Agent response group: session=%s destination=%s",
                    target.session_id,
                    target.destination_id,
                )
                self._mark_agent_failed(tuple(grouped), error_kind="submit_failed")

    def _prepare_occurrence(
        self,
        occurrence: ReminderOccurrence,
        *,
        state: PresenceState,
        now: datetime,
    ) -> tuple[_AgentResponseTarget, ReminderOccurrence] | None:
        if not self._todos.occurrence_is_deliverable(occurrence.id):
            return None
        target, current = self._resolve_or_freeze(occurrence, state=state, now=now)
        self._append_reminder_fact(current, target, now=now)
        self._deliver_fixed_projections(current, target=target, state=state, now=now)
        current = self._todos.update_occurrence(
            current.id,
            DeliveryUpdate(
                state=OccurrenceState.DELIVERED,
                destination_kind="conversation",
                destination_id=target.session_id,
                delivery_receipt=current.id,
            ),
            now=now,
        )
        if current.agent_response_status is not AgentResponseStatus.PENDING:
            return None
        return (
            _AgentResponseTarget(
                session_id=target.session_id,
                destination_id=target.destination_id,
                interaction_surface=target.interaction_surface,
            ),
            current,
        )

    def _resolve_or_freeze(
        self,
        occurrence: ReminderOccurrence,
        *,
        state: PresenceState,
        now: datetime,
    ) -> tuple[_PersistenceTarget, ReminderOccurrence]:
        if occurrence.persistence_session_id is not None:
            target = _target_from_frozen(occurrence)
            self._sessions.load(target.session_id)
            return target, occurrence

        valid_im = tuple(
            item for item in state.im_bindings if self._session_exists(item.session_id)
        )
        target = self._select_persistence_target(occurrence, valid_im)
        valid_state = replace(state, im_bindings=valid_im)
        mode = self._notification_mode_provider()
        decision_kind: str
        if mode is ReminderNotificationMode.SMART:
            decision = self._policy.decide(valid_state)
            self._presence.record_decision(decision)
            decision_kind = decision.kind
        else:
            decision_kind = "all"
        current = self._todos.freeze_occurrence_context(
            occurrence.id,
            persistence_session_id=target.session_id,
            persistence_destination_id=target.destination_id,
            persistence_reason=target.reason,
            notification_mode=mode,
            notification_decision_kind=decision_kind,
            now=now,
        )
        return _target_from_frozen(current), current

    def _select_persistence_target(
        self,
        occurrence: ReminderOccurrence,
        candidates: tuple[IMPresenceSnapshot, ...],
    ) -> _PersistenceTarget:
        candidates_by_session: dict[str, list[IMPresenceSnapshot]] = {}
        for candidate in candidates:
            candidates_by_session.setdefault(candidate.session_id, []).append(candidate)

        session_interactions = {
            session_id: self._latest_user_interaction_at(session_id)
            for session_id in candidates_by_session
        }
        interacted_sessions = {
            session_id: interaction_at
            for session_id, interaction_at in session_interactions.items()
            if interaction_at is not None
        }
        reason: str
        selected_session_id: str | None = None
        if interacted_sessions:
            selected_session_id = max(
                interacted_sessions,
                key=lambda session_id: (
                    interacted_sessions[session_id],
                    session_id,
                ),
            )
            reason = "latest_session_user_interaction"
        elif candidates_by_session:
            reminder = self._todos.get_reminder(occurrence.reminder_id)
            origin_session_id = reminder.origin_session_id
            if origin_session_id in candidates_by_session:
                selected_session_id = origin_session_id
                reason = "origin_im_session_without_user_interaction"
            else:
                selected_session_id = max(candidates_by_session)
                reason = "stable_im_session_without_user_interaction"

        if selected_session_id is not None:
            selected = max(
                candidates_by_session[selected_session_id],
                key=_im_destination_key,
            )
            return _PersistenceTarget(
                session_id=selected.session_id,
                destination_id=selected.destination_id,
                interaction_surface=InteractionSurfaceId(f"im:{selected.im_type}"),
                reason=reason,
            )

        reminder = self._todos.get_reminder(occurrence.reminder_id)
        origin_session_id = reminder.origin_session_id
        if origin_session_id and self._session_exists(origin_session_id):
            return _PersistenceTarget(
                session_id=origin_session_id,
                destination_id=None,
                interaction_surface=DESKTOP_CHAT_SURFACE,
                reason="origin_session",
            )
        raise ReminderRoutingError(
            f"Reminder occurrence has no valid IM binding or origin session: {occurrence.id}"
        )

    def _latest_user_interaction_at(self, session_id: str) -> datetime | None:
        session = self._sessions.load(session_id)
        for event in reversed(session.active_events):
            if event.type == "user_message":
                return event.ts.astimezone(UTC)
        return None

    def _deliver_fixed_projections(
        self,
        occurrence: ReminderOccurrence,
        *,
        target: _PersistenceTarget,
        state: PresenceState,
        now: datetime,
    ) -> None:
        decision_kind = occurrence.notification_decision_kind or "none"
        im_destination_id = target.destination_id
        pet_planned = decision_kind in {"pet", "all"}

        if im_destination_id is not None and decision_kind in {"im", "all"}:
            im_fixed = self._todos.ensure_delivery(
                occurrence.id,
                ReminderProjectionKind.IM_FIXED,
                destination_id=im_destination_id,
                now=now,
            )
            self._todos.ensure_delivery(
                occurrence.id,
                ReminderProjectionKind.IM_AGENT,
                destination_id=im_destination_id,
                now=now,
            )
            if im_fixed.state is ReminderDeliveryState.PENDING:
                _attempt, started = self._todos.begin_delivery_attempt(
                    occurrence.id,
                    ReminderProjectionKind.IM_FIXED,
                    now=now,
                )
                if started:
                    result = self._deliver_im_fixed(occurrence, im_destination_id)
                    self._record_send_result(
                        occurrence.id,
                        ReminderProjectionKind.IM_FIXED,
                        result,
                        now=now,
                    )

        if pet_planned:
            pet = self._todos.ensure_delivery(
                occurrence.id,
                ReminderProjectionKind.PET_FIXED,
                destination_id="pet",
                now=now,
            )
            if pet.state is ReminderDeliveryState.PENDING:
                if not state.pet_available:
                    self._todos.update_delivery(
                        occurrence.id,
                        ReminderProjectionKind.PET_FIXED,
                        state=ReminderDeliveryState.SKIPPED,
                        error_kind="pet_unavailable",
                        now=now,
                        increment_attempt=False,
                    )
                else:
                    if self._pet_notification is not None:
                        # New ledger owns presentation. This legacy row records only
                        # delegation, never proof that a user has seen the bubble.
                        try:
                            notification_id = self._pet_notification(occurrence)
                        except Exception:
                            logger.exception(
                                "Reminder notification delegation failed: %s", occurrence.id
                            )
                            return  # Preserve the independent conversation/IM Agent path.
                        self._todos.update_delivery(
                            occurrence.id,
                            ReminderProjectionKind.PET_FIXED,
                            state=ReminderDeliveryState.SKIPPED,
                            receipt=notification_id,
                            error_kind="delegated_to_notification_manager",
                            now=now,
                            increment_attempt=False,
                        )
                        return
                    _attempt, started = self._todos.begin_delivery_attempt(
                        occurrence.id,
                        ReminderProjectionKind.PET_FIXED,
                        now=now,
                    )
                    if started:
                        self._agent_runtime.listeners.publish_fixed_ambient(
                            source_kind="todo_reminder",
                            text=_fixed_text(occurrence),
                            occurrence_id=occurrence.id,
                        )
                        self._todos.update_delivery(
                            occurrence.id,
                            ReminderProjectionKind.PET_FIXED,
                            state=ReminderDeliveryState.DELIVERED,
                            receipt=occurrence.id,
                            now=now,
                            increment_attempt=False,
                        )

    def _append_reminder_fact(
        self,
        occurrence: ReminderOccurrence,
        target: _PersistenceTarget,
        *,
        now: datetime,
    ) -> None:
        session = self._sessions.load(target.session_id)
        if any(
            event.type == "todo_reminder" and event.uuid == occurrence.id
            for event in session.events
        ):
            return
        facts = occurrence.facts
        self._sessions.append_event(
            target.session_id,
            Event(
                type="todo_reminder",
                uuid=occurrence.id,
                ts=now,
                payload={
                    "occurrence_id": occurrence.id,
                    "todo_id": facts.todo_id,
                    "reminder_id": facts.reminder_id,
                    "title": facts.title,
                    "description": facts.description,
                    "scheduled_at_utc": facts.scheduled_at_utc.isoformat(),
                    "scheduled_local": facts.scheduled_local.isoformat(),
                    "timezone": facts.timezone,
                    "recurrence": facts.recurrence,
                    "todo_status": facts.todo_status.value,
                    "occurrence_status": "delivered",
                    "delivered_at": now.isoformat(),
                    "destination_kind": "conversation",
                    "persistence_reason": target.reason,
                    "presentation": "conversation",
                },
            ),
        )

    def _deliver_im_fixed(
        self,
        occurrence: ReminderOccurrence,
        destination_id: str,
    ) -> OutboundSendResult:
        from .protocols.im.provider import OutboundSendResult

        if self._im_runtime is None:
            return OutboundSendResult("failed", error_kind="im_runtime_unavailable")
        return self._im_runtime.send_proactive(destination_id, _fixed_text(occurrence))

    def _record_send_result(
        self,
        occurrence_id: str,
        projection_kind: ReminderProjectionKind,
        result: OutboundSendResult,
        *,
        now: datetime,
    ) -> None:
        if result.outcome == "delivered":
            state = ReminderDeliveryState.DELIVERED
        elif result.outcome == "unknown":
            state = ReminderDeliveryState.UNKNOWN
        else:
            state = ReminderDeliveryState.FAILED
        self._todos.update_delivery(
            occurrence_id,
            projection_kind,
            state=state,
            receipt=result.provider_message_id,
            error_kind=result.error_kind,
            now=now,
            increment_attempt=False,
        )

    def _start_agent_response(
        self,
        occurrences: tuple[ReminderOccurrence, ...],
        *,
        target: _AgentResponseTarget,
        batch_id: str,
    ) -> None:
        now = self._now()
        occurrence_ids = tuple(occurrence.id for occurrence in occurrences)
        for occurrence_id in occurrence_ids:
            self._todos.update_agent_response_status(
                occurrence_id, AgentResponseStatus.RUNNING, now=now
            )
        handle = self._agent_runtime.submit(
            SystemTriggerEvent(
                session_id=target.session_id,
                source_kind="todo_reminder",
                system_prompt_addendum=_structured_events(occurrences),
                output_visibility="user",
                tool_mode="normal",
                presentation="conversation",
                event_metadata={
                    "batch_id": batch_id,
                    "occurrence_ids": list(occurrence_ids),
                },
            ),
            dedupe_key=(
                f"todo-reminder-response:{batch_id}:{target.session_id}:"
                f"{target.destination_id or 'conversation'}"
            ),
            channel="text",
            surface=target.interaction_surface,
            metadata={
                "protocol": "todo_reminder",
                "batch_id": batch_id,
                "occurrence_ids": list(occurrence_ids),
                "interaction_destination_id": target.destination_id or target.session_id,
            },
        )
        thread = threading.Thread(
            target=self._watch_agent_response,
            args=(occurrence_ids, handle, target, batch_id),
            daemon=True,
            name=f"ReminderAgentResponse-{batch_id[:8]}",
        )
        thread.start()

    def _watch_agent_response(
        self,
        occurrence_ids: tuple[str, ...],
        handle: RunHandle,
        target: _AgentResponseTarget,
        batch_id: str,
    ) -> None:
        text_parts: list[str] = []
        try:
            for event in handle.events():
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
            terminal = handle.terminal
            text = "".join(text_parts).strip()
            success = (
                terminal is not None and terminal.outcome is RunOutcome.COMPLETED and bool(text)
            )
            if success:
                self._project_agent_response_to_im(occurrence_ids, target=target, text=text)
                status = AgentResponseStatus.COMPLETED
            else:
                self._fail_pending_agent_projections(
                    occurrence_ids, error_kind="agent_response_failed"
                )
                status = AgentResponseStatus.FAILED
        except Exception:
            logger.exception("Reminder Agent response watcher failed: %s", batch_id)
            self._fail_pending_agent_projections(occurrence_ids, error_kind="watcher_failed")
            status = AgentResponseStatus.FAILED
        for occurrence_id in occurrence_ids:
            self._todos.update_agent_response_status(occurrence_id, status, now=self._now())

    def _project_agent_response_to_im(
        self,
        occurrence_ids: tuple[str, ...],
        *,
        target: _AgentResponseTarget,
        text: str,
    ) -> None:
        eligible: list[str] = []
        for occurrence_id in occurrence_ids:
            try:
                agent_delivery = self._todos.get_delivery(
                    occurrence_id, ReminderProjectionKind.IM_AGENT
                )
                fixed_delivery = self._todos.get_delivery(
                    occurrence_id, ReminderProjectionKind.IM_FIXED
                )
            except TodoError:
                continue
            if agent_delivery.state is not ReminderDeliveryState.PENDING:
                continue
            if fixed_delivery.state is ReminderDeliveryState.DELIVERED:
                eligible.append(occurrence_id)
            else:
                self._todos.update_delivery(
                    occurrence_id,
                    ReminderProjectionKind.IM_AGENT,
                    state=ReminderDeliveryState.SKIPPED,
                    error_kind=f"im_fixed_{fixed_delivery.state.value}",
                    now=self._now(),
                    increment_attempt=False,
                )
        started_occurrence_ids: list[str] = []
        for occurrence_id in eligible:
            _attempt, started = self._todos.begin_delivery_attempt(
                occurrence_id,
                ReminderProjectionKind.IM_AGENT,
                now=self._now(),
            )
            if started:
                started_occurrence_ids.append(occurrence_id)
        if not started_occurrence_ids:
            return
        if self._im_runtime is None or target.destination_id is None:
            for occurrence_id in started_occurrence_ids:
                self._todos.update_delivery(
                    occurrence_id,
                    ReminderProjectionKind.IM_AGENT,
                    state=ReminderDeliveryState.FAILED,
                    error_kind="im_runtime_unavailable",
                    now=self._now(),
                    increment_attempt=False,
                )
            return
        result = self._im_runtime.send_proactive(
            target.destination_id,
            text,
            text_format="markdown",
        )
        for occurrence_id in started_occurrence_ids:
            self._record_send_result(
                occurrence_id,
                ReminderProjectionKind.IM_AGENT,
                result,
                now=self._now(),
            )

    def _fail_pending_agent_projections(
        self,
        occurrence_ids: tuple[str, ...],
        *,
        error_kind: str,
    ) -> None:
        for occurrence_id in occurrence_ids:
            try:
                delivery = self._todos.get_delivery(occurrence_id, ReminderProjectionKind.IM_AGENT)
            except TodoError:
                continue
            if delivery.state is ReminderDeliveryState.PENDING:
                self._todos.update_delivery(
                    occurrence_id,
                    ReminderProjectionKind.IM_AGENT,
                    state=ReminderDeliveryState.FAILED,
                    error_kind=error_kind,
                    now=self._now(),
                    increment_attempt=False,
                )

    def _mark_agent_failed(
        self,
        occurrences: tuple[ReminderOccurrence, ...],
        *,
        error_kind: str,
    ) -> None:
        self._fail_pending_agent_projections(
            tuple(occurrence.id for occurrence in occurrences),
            error_kind=error_kind,
        )
        for occurrence in occurrences:
            self._todos.update_agent_response_status(
                occurrence.id, AgentResponseStatus.FAILED, now=self._now()
            )

    def _mark_delivery_failed(
        self,
        occurrence: ReminderOccurrence,
        *,
        now: datetime,
    ) -> None:
        if not self._todos.occurrence_is_deliverable(occurrence.id):
            return
        self._todos.update_occurrence(
            occurrence.id,
            DeliveryUpdate(
                state=OccurrenceState.FAILED,
                next_attempt_at=now + timedelta(minutes=1),
            ),
            now=now,
        )

    def _session_exists(self, session_id: str) -> bool:
        try:
            self._sessions.load(session_id)
        except SessionNotFoundError:
            return False
        return True

    def _now(self) -> datetime:
        value = self._clock()
        return value.astimezone(UTC)


def _target_from_frozen(occurrence: ReminderOccurrence) -> _PersistenceTarget:
    session_id = occurrence.persistence_session_id
    if session_id is None:
        raise ReminderRoutingError(f"Occurrence routing is not frozen: {occurrence.id}")
    destination_id = occurrence.persistence_destination_id
    surface = (
        InteractionSurfaceId(f"im:{destination_id.split('|', 1)[0]}")
        if destination_id
        else DESKTOP_CHAT_SURFACE
    )
    return _PersistenceTarget(
        session_id=session_id,
        destination_id=destination_id,
        interaction_surface=surface,
        reason=occurrence.persistence_reason or "frozen",
    )


def _im_destination_key(item: IMPresenceSnapshot) -> tuple[datetime, str]:
    interaction_at = item.last_interaction_at or datetime.min.replace(tzinfo=UTC)
    return interaction_at.astimezone(UTC), item.destination_id


def _fixed_text(occurrence: ReminderOccurrence) -> str:
    facts = occurrence.facts
    lines = [
        f"提醒：{facts.title}",
        f"计划时间：{facts.scheduled_local:%Y-%m-%d %H:%M} ({facts.timezone})",
    ]
    if facts.description:
        lines.append(f"说明：{facts.description}")
    if facts.recurrence != "once":
        lines.append(f"周期：{facts.recurrence}")
    return "\n".join(lines)


def _structured_events(occurrences: tuple[ReminderOccurrence, ...]) -> str:
    events = []
    for occurrence in occurrences:
        facts = occurrence.facts
        events.append(
            {
                "type": "todo_reminder",
                "payload": {
                    "occurrence_id": occurrence.id,
                    "todo_id": facts.todo_id,
                    "reminder_id": facts.reminder_id,
                    "title": facts.title,
                    "description": facts.description,
                    "scheduled_at_utc": facts.scheduled_at_utc.isoformat(),
                    "scheduled_local": facts.scheduled_local.isoformat(),
                    "timezone": facts.timezone,
                    "recurrence": facts.recurrence,
                    "todo_status": facts.todo_status.value,
                },
            }
        )
    payload = json.dumps(events, ensure_ascii=False, separators=(",", ":"))
    return f"<system_events>\n{payload}\n</system_events>"


def _batch_id(occurrence_ids: tuple[str, ...]) -> str:
    source = "\n".join(occurrence_ids).encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:32]


__all__ = ["ReminderDeliveryOrchestrator", "ReminderRoutingError"]
