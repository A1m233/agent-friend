"""Reminder occurrence 的单目的地投递与默认 Agent 回应编排。"""

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
    ReminderOccurrence,
    TodoStore,
)

from agent import Event, SessionNotFoundError

from .presence import DestinationPolicy, PresenceRegistry, PresenceState

if TYPE_CHECKING:
    from agent.runtime.models import RunHandle

    from .protocols.im.provider import OutboundSendResult
    from .protocols.im.runtime import IMRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AgentResponseTarget:
    session_id: str
    destination_kind: str
    destination_id: str
    interaction_surface: InteractionSurfaceId


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._todos = todo_store
        self._sessions = session_store
        self._agent_runtime = agent_runtime
        self._presence = presence_registry
        self._im_runtime = im_runtime
        self._policy = policy or DestinationPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))

    def deliver(self, occurrence: ReminderOccurrence) -> None:
        """兼容单 occurrence 调用方；单条提醒仍走统一批协议。"""
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
                target = self._deliver_fixed(occurrence, state=state, now=now)
            except Exception:
                logger.exception("Reminder occurrence delivery failed: %s", occurrence.id)
                self._mark_delivery_failed(occurrence, now=now)
                continue
            if target is not None:
                response_groups.setdefault(target, []).append(occurrence)

        for target, grouped in response_groups.items():
            try:
                self._start_agent_response(tuple(grouped), target=target, batch_id=batch_id)
            except Exception:
                logger.exception(
                    "Failed to start reminder Agent response group: session=%s destination=%s",
                    target.session_id,
                    target.destination_id,
                )
                for occurrence in grouped:
                    self._todos.update_agent_response_status(
                        occurrence.id, AgentResponseStatus.FAILED, now=self._now()
                    )

    def _deliver_fixed(
        self,
        occurrence: ReminderOccurrence,
        *,
        state: PresenceState,
        now: datetime,
    ) -> _AgentResponseTarget | None:
        if not self._todos.occurrence_is_deliverable(occurrence.id):
            return None
        remaining = state
        for _ in range(4):
            decision = self._policy.decide(remaining)
            self._presence.record_decision(decision)
            if decision.kind == "desktop" and decision.session_id:
                if self._deliver_desktop(occurrence, decision.session_id, now=now):
                    return _AgentResponseTarget(
                        session_id=decision.session_id,
                        destination_kind="desktop",
                        destination_id=decision.destination_id or decision.session_id,
                        interaction_surface=DESKTOP_CHAT_SURFACE,
                    )
                remaining = replace(remaining, desktop=None)
                continue
            if decision.kind == "im" and decision.destination_id and decision.session_id:
                if not self._todos.occurrence_is_deliverable(occurrence.id):
                    return None
                interaction_surface = _interaction_surface_for_im(
                    remaining, decision.destination_id
                )
                result = self._deliver_im_fixed(occurrence, decision.destination_id)
                if result.outcome == "delivered":
                    self._todos.update_occurrence(
                        occurrence.id,
                        DeliveryUpdate(
                            state=OccurrenceState.DELIVERED,
                            destination_kind="im",
                            destination_id=decision.destination_id,
                            delivery_receipt=result.provider_message_id,
                        ),
                        now=now,
                    )
                    try:
                        self._append_reminder_fact(
                            occurrence,
                            decision.session_id,
                            now=now,
                            destination_kind="im",
                            presentation="none",
                        )
                    except SessionNotFoundError:
                        # 外部消息已经成功送达，不能因为会话审计事实缺失而重试并重复发送。
                        logger.warning(
                            "Delivered IM reminder session is missing: occurrence=%s session=%s",
                            occurrence.id,
                            decision.session_id,
                        )
                        self._todos.update_agent_response_status(
                            occurrence.id, AgentResponseStatus.FAILED, now=now
                        )
                        return None
                    except Exception:
                        # occurrence 已先标 delivered；任何会话落盘错误都不能把外部副作用
                        # 重新变成可重试发送，只损失本次上下文增强并保留错误证据。
                        logger.exception(
                            "Failed to persist delivered IM reminder fact: occurrence=%s session=%s",
                            occurrence.id,
                            decision.session_id,
                        )
                        self._todos.update_agent_response_status(
                            occurrence.id, AgentResponseStatus.FAILED, now=now
                        )
                        return None
                    return _AgentResponseTarget(
                        session_id=decision.session_id,
                        destination_kind="im",
                        destination_id=decision.destination_id,
                        interaction_surface=interaction_surface,
                    )
                if result.outcome == "unknown":
                    self._todos.update_occurrence(
                        occurrence.id,
                        DeliveryUpdate(
                            state=OccurrenceState.DELIVERY_UNKNOWN,
                            destination_kind="im",
                            destination_id=decision.destination_id,
                            delivery_receipt=result.error_kind,
                            agent_response_status=AgentResponseStatus.SKIPPED,
                        ),
                        now=now,
                    )
                    return None
                remaining = replace(
                    remaining,
                    im_bindings=tuple(
                        item
                        for item in remaining.im_bindings
                        if item.destination_id != decision.destination_id
                    ),
                )
                continue
            if decision.kind == "pet":
                if not self._todos.occurrence_is_deliverable(occurrence.id):
                    return None
                self._agent_runtime.listeners.publish_fixed_ambient(
                    source_kind="todo_reminder",
                    text=_fixed_text(occurrence),
                    occurrence_id=occurrence.id,
                )
                self._todos.update_occurrence(
                    occurrence.id,
                    DeliveryUpdate(
                        state=OccurrenceState.DELIVERED,
                        destination_kind="pet",
                        destination_id="pet",
                        delivery_receipt=occurrence.id,
                        agent_response_status=AgentResponseStatus.SKIPPED,
                    ),
                    now=now,
                )
                return None
            self._todos.update_occurrence(
                occurrence.id,
                DeliveryUpdate(
                    state=OccurrenceState.FAILED,
                    next_attempt_at=now + timedelta(minutes=1),
                    agent_response_status=AgentResponseStatus.PENDING,
                ),
                now=now,
            )
            return None
        self._todos.update_occurrence(
            occurrence.id,
            DeliveryUpdate(
                state=OccurrenceState.FAILED,
                next_attempt_at=now + timedelta(minutes=1),
            ),
            now=now,
        )
        return None

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

    def _deliver_desktop(
        self,
        occurrence: ReminderOccurrence,
        session_id: str,
        *,
        now: datetime,
    ) -> bool:
        if not self._todos.occurrence_is_deliverable(occurrence.id):
            return False
        try:
            self._append_reminder_fact(
                occurrence,
                session_id,
                now=now,
                destination_kind="desktop",
                presentation="conversation",
            )
        except SessionNotFoundError:
            return False

        self._todos.update_occurrence(
            occurrence.id,
            DeliveryUpdate(
                state=OccurrenceState.DELIVERED,
                destination_kind="desktop",
                destination_id=session_id,
                delivery_receipt=occurrence.id,
            ),
            now=now,
        )
        return True

    def _append_reminder_fact(
        self,
        occurrence: ReminderOccurrence,
        session_id: str,
        *,
        now: datetime,
        destination_kind: str,
        presentation: str,
    ) -> None:
        session = self._sessions.load(session_id)
        if not any(
            event.type == "todo_reminder" and event.uuid == occurrence.id
            for event in session.events
        ):
            facts = occurrence.facts
            self._sessions.append_event(
                session_id,
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
                        "destination_kind": destination_kind,
                        "presentation": presentation,
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
        try:
            handle = self._agent_runtime.submit(
                SystemTriggerEvent(
                    session_id=target.session_id,
                    source_kind="todo_reminder",
                    system_prompt_addendum=_structured_events(occurrences),
                    output_visibility="user",
                    tool_mode="normal",
                    # IM 的固定事实已经投递到唯一目标出口；Agent 回应仍借目标
                    # session 生成，但不能因此再次出现在桌面对话里。
                    presentation=(
                        "conversation" if target.destination_kind == "desktop" else "none"
                    ),
                    event_metadata={
                        "batch_id": batch_id,
                        "occurrence_ids": list(occurrence_ids),
                    },
                ),
                dedupe_key=(
                    f"todo-reminder-response:{batch_id}:"
                    f"{target.destination_kind}:{target.destination_id}"
                ),
                channel="text",
                surface=target.interaction_surface,
                metadata={
                    "protocol": "todo_reminder",
                    "batch_id": batch_id,
                    "occurrence_ids": list(occurrence_ids),
                    "interaction_destination_id": target.destination_id,
                },
            )
        except Exception:
            logger.exception("Failed to submit reminder Agent response batch: %s", batch_id)
            for occurrence_id in occurrence_ids:
                self._todos.update_agent_response_status(
                    occurrence_id, AgentResponseStatus.FAILED, now=self._now()
                )
            return

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
            if success and target.destination_kind == "im":
                if self._im_runtime is None:
                    success = False
                else:
                    success = (
                        self._im_runtime.send_proactive(
                            target.destination_id,
                            text,
                            text_format="markdown",
                        ).outcome
                        == "delivered"
                    )
            status = AgentResponseStatus.COMPLETED if success else AgentResponseStatus.FAILED
        except Exception:
            logger.exception("Reminder Agent response watcher failed: %s", batch_id)
            status = AgentResponseStatus.FAILED
        for occurrence_id in occurrence_ids:
            self._todos.update_agent_response_status(occurrence_id, status, now=self._now())

    def _now(self) -> datetime:
        value = self._clock()
        return value.astimezone(UTC)


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


def _interaction_surface_for_im(
    state: PresenceState,
    destination_id: str,
) -> InteractionSurfaceId:
    binding = next(
        (item for item in state.im_bindings if item.destination_id == destination_id),
        None,
    )
    if binding is None:
        raise RuntimeError(f"IM presence binding missing for destination: {destination_id}")
    return InteractionSurfaceId(f"im:{binding.im_type}")


__all__ = ["ReminderDeliveryOrchestrator"]
