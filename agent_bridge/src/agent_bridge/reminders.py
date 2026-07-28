"""Reminder occurrence 的单目的地投递与默认 Agent 回应编排。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agent.conversation_events import TextDelta
from agent.runtime import AgentRuntime, RunOutcome, SystemTriggerEvent
from agent.sessions.store import SessionStore
from agent.todos import (
    AgentResponseStatus,
    DeliveryUpdate,
    OccurrenceState,
    ReminderOccurrence,
    TodoStore,
)

from agent import Event, SessionNotFoundError

from .presence import DestinationPolicy, PresenceRegistry

if TYPE_CHECKING:
    from agent.runtime.models import RunHandle

    from .protocols.im.provider import OutboundSendResult
    from .protocols.im.runtime import IMRuntime

logger = logging.getLogger(__name__)


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
        now = self._now()
        if not self._todos.occurrence_is_deliverable(occurrence.id):
            return
        if self._im_runtime is not None:
            self._presence.replace_im(self._im_runtime.presence_snapshots())
        state = self._presence.snapshot(
            now=now,
            pet_available=self._agent_runtime.listeners.subscriber_count() > 0,
        )
        remaining = state
        for _ in range(4):
            decision = self._policy.decide(remaining)
            self._presence.record_decision(decision)
            if decision.kind == "desktop" and decision.session_id:
                if self._deliver_desktop(occurrence, decision.session_id, now=now):
                    self._start_agent_response(
                        occurrence,
                        session_id=decision.session_id,
                        destination_kind="desktop",
                        destination_id=decision.destination_id or decision.session_id,
                    )
                    return
                remaining = replace(remaining, desktop=None)
                continue
            if decision.kind == "im" and decision.destination_id and decision.session_id:
                if not self._todos.occurrence_is_deliverable(occurrence.id):
                    return
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
                    except Exception:
                        # occurrence 已先标 delivered；任何会话落盘错误都不能把外部副作用
                        # 重新变成可重试发送，只损失本次上下文增强并保留错误证据。
                        logger.exception(
                            "Failed to persist delivered IM reminder fact: occurrence=%s session=%s",
                            occurrence.id,
                            decision.session_id,
                        )
                    self._start_agent_response(
                        occurrence,
                        session_id=decision.session_id,
                        destination_kind="im",
                        destination_id=decision.destination_id,
                    )
                    return
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
                    return
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
                    return
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
                return
            self._todos.update_occurrence(
                occurrence.id,
                DeliveryUpdate(
                    state=OccurrenceState.FAILED,
                    next_attempt_at=now + timedelta(minutes=1),
                    agent_response_status=AgentResponseStatus.PENDING,
                ),
                now=now,
            )
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
        occurrence: ReminderOccurrence,
        *,
        session_id: str,
        destination_kind: str,
        destination_id: str,
    ) -> None:
        now = self._now()
        self._todos.update_agent_response_status(
            occurrence.id, AgentResponseStatus.RUNNING, now=now
        )
        try:
            handle = self._agent_runtime.submit(
                SystemTriggerEvent(
                    session_id=session_id,
                    source_kind="todo_reminder",
                    system_prompt_addendum=_agent_prompt(occurrence),
                    output_visibility="user",
                    # IM 的固定事实已经投递到唯一目标出口；Agent 回应仍借目标
                    # session 生成，但不能因此再次出现在桌面对话里。
                    presentation="conversation" if destination_kind == "desktop" else "none",
                    event_metadata={"occurrence_id": occurrence.id},
                ),
                dedupe_key=f"todo-reminder-response:{occurrence.id}",
                metadata={"protocol": "todo_reminder", "occurrence_id": occurrence.id},
            )
        except Exception:
            logger.exception("Failed to submit reminder Agent response: %s", occurrence.id)
            self._todos.update_agent_response_status(
                occurrence.id, AgentResponseStatus.FAILED, now=self._now()
            )
            return

        thread = threading.Thread(
            target=self._watch_agent_response,
            args=(occurrence.id, handle, destination_kind, destination_id),
            daemon=True,
            name=f"ReminderAgentResponse-{occurrence.id[:8]}",
        )
        thread.start()

    def _watch_agent_response(
        self,
        occurrence_id: str,
        handle: RunHandle,
        destination_kind: str,
        destination_id: str,
    ) -> None:
        text_parts: list[str] = []
        try:
            for event in handle.events():
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
            terminal = handle.terminal
            success = terminal is not None and terminal.outcome is RunOutcome.COMPLETED
            if success and destination_kind == "im":
                text = "".join(text_parts).strip()
                if not text or self._im_runtime is None:
                    success = False
                else:
                    success = (
                        self._im_runtime.send_proactive(destination_id, text).outcome == "delivered"
                    )
            status = AgentResponseStatus.COMPLETED if success else AgentResponseStatus.FAILED
        except Exception:
            logger.exception("Reminder Agent response watcher failed: %s", occurrence_id)
            status = AgentResponseStatus.FAILED
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


def _agent_prompt(occurrence: ReminderOccurrence) -> str:
    facts = occurrence.facts
    return (
        "<system_trigger>\n"
        "一条 Todo Reminder 的固定事实已经先展示给用户。请结合当前会话和 persona，"
        "用简短自然的一两句话回应。不要重复伪造时间，不要改写以下事实，不要声称用户已完成，"
        "不要调用工具。\n"
        f"Todo: {facts.title}\n"
        f"说明: {facts.description or '无'}\n"
        f"计划时间: {facts.scheduled_local.isoformat()} [{facts.timezone}]\n"
        f"周期: {facts.recurrence}\n"
        "</system_trigger>"
    )


__all__ = ["ReminderDeliveryOrchestrator"]
