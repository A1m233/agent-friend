from __future__ import annotations

import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

from agent.conversation_events import TextDelta
from agent.runtime import AgentRuntime, RunOutcome
from agent.todos import (
    OnceSchedule,
    RecurringSchedule,
    ReminderDeliveryState,
    ReminderNotificationMode,
    ReminderOccurrence,
    ReminderProjectionKind,
    TodoService,
    TodoStore,
)
from agent_bridge.presence import DesktopPresenceSnapshot, IMPresenceSnapshot, PresenceRegistry
from agent_bridge.protocols.im.content import OutboundTextFormat
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.reminders import ReminderDeliveryOrchestrator

from agent import Event, JsonlSessionStore, Session

NOW = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)


class FakeHandle:
    terminal = SimpleNamespace(outcome=RunOutcome.COMPLETED)

    def __init__(self, response_text: str = "已提醒。") -> None:
        self.response_text = response_text

    def events(self):  # type: ignore[no-untyped-def]
        if not self.response_text:
            return iter(())
        return iter((TextDelta(text=self.response_text),))


class FakeListeners:
    def __init__(self, subscribers: int) -> None:
        self.subscribers = subscribers
        self.ambient: list[dict[str, str]] = []

    def subscriber_count(self) -> int:
        return self.subscribers

    def publish_fixed_ambient(self, **payload: str) -> None:
        self.ambient.append(payload)


class FakeAgentRuntime:
    def __init__(self, subscribers: int = 0, response_text: str = "已提醒。") -> None:
        self.listeners = FakeListeners(subscribers)
        self.response_text = response_text
        self.submitted: list[Any] = []
        self.submit_kwargs: list[dict[str, Any]] = []

    def submit(self, event, **kwargs):  # type: ignore[no-untyped-def]
        self.submitted.append(event)
        self.submit_kwargs.append(kwargs)
        return FakeHandle(self.response_text)


class FakeIMRuntime:
    def __init__(
        self,
        snapshots: tuple[IMPresenceSnapshot, ...],
        outcome: str = "delivered",
        error_kind: str = "test",
    ) -> None:
        self.snapshots = snapshots
        self.outcome = outcome
        self.error_kind = error_kind
        self.sent: list[tuple[str, str, OutboundTextFormat]] = []
        self.response_sent = threading.Event()

    def presence_snapshots(self) -> tuple[IMPresenceSnapshot, ...]:
        return self.snapshots

    def send_proactive(
        self,
        destination_id: str,
        text: str,
        *,
        text_format: OutboundTextFormat = "plain_text",
    ) -> OutboundSendResult:
        self.sent.append((destination_id, text, text_format))
        if text_format == "markdown":
            self.response_sent.set()
        return OutboundSendResult(cast(Any, self.outcome), error_kind=self.error_kind)


def _im(
    session_id: str,
    *,
    bind: str,
    online: bool = True,
    last_interaction_at: datetime | None = None,
    binding_updated_at: datetime | None = None,
) -> IMPresenceSnapshot:
    return IMPresenceSnapshot(
        im_type="qq",
        provider_bind_id=bind,
        chat_scope="c2c",
        chat_id=f"chat-{bind}",
        session_id=session_id,
        online=online,
        last_interaction_at=last_interaction_at,
        binding_updated_at=binding_updated_at,
    )


def _claimed_many(
    tmp_path: Path,
    titles: tuple[str, ...],
    *,
    origin_session_id: str | None = "origin-session",
) -> tuple[TodoStore, tuple[ReminderOccurrence, ...]]:
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    for title in titles:
        todo = service.create(title=title, description="带齐附件")
        service.add_reminder(
            todo.todo.id,
            schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
            origin_session_id=origin_session_id,
        )
    store.create_due_occurrences(NOW)
    return store, tuple(store.claim_pending(NOW))


def _claimed(
    tmp_path: Path,
    *,
    origin_session_id: str | None = "origin-session",
) -> tuple[TodoStore, ReminderOccurrence]:
    store, occurrences = _claimed_many(tmp_path, ("提交材料",), origin_session_id=origin_session_id)
    return store, occurrences[0]


def _sessions(tmp_path: Path, *session_ids: str) -> JsonlSessionStore:
    store = JsonlSessionStore(tmp_path / "sessions")
    for session_id in session_ids:
        store.create(
            Session.new(title=session_id, persona="default", model="fake", session_id=session_id)
        )
    return store


def _append_user_message(
    store: JsonlSessionStore,
    session_id: str,
    *,
    at: datetime,
    content: str = "hello",
) -> None:
    store.append_event(
        session_id,
        Event(
            type="user_message",
            uuid=f"user-{session_id}-{at.isoformat()}",
            ts=at,
            payload={"content": content},
        ),
    )


def _append_system_output(
    store: JsonlSessionStore,
    session_id: str,
    *,
    event_type: Literal["assistant_message", "todo_reminder"],
    at: datetime,
) -> None:
    payload = (
        {"content": "system output", "partial": False}
        if event_type == "assistant_message"
        else {"title": "reminder output"}
    )
    store.append_event(
        session_id,
        Event(
            type=event_type,
            uuid=f"system-{event_type}-{session_id}-{at.isoformat()}",
            ts=at,
            payload=payload,
        ),
    )


def _desktop_presence(session_id: str = "other-desktop") -> PresenceRegistry:
    presence = PresenceRegistry()
    presence.update_desktop(
        DesktopPresenceSnapshot(
            observed_at=NOW,
            current_session_id=session_id,
            window_visible=True,
            window_focused=True,
            window_minimized=False,
            os_idle_ms=100,
            session_state="active",
            last_desktop_interaction_at=NOW,
            capabilities=("idle", "lock", "window"),
        )
    )
    return presence


def _wait_status(store: TodoStore, occurrence_ids: tuple[str, ...], expected: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        statuses = [
            store.get_occurrence(item).agent_response_status.value for item in occurrence_ids
        ]
        if statuses == [expected] * len(occurrence_ids):
            return
        time.sleep(0.01)
    assert statuses == [expected] * len(occurrence_ids)


def test_no_im_binding_uses_reminder_origin_and_persists_conversation_fact(
    tmp_path: Path,
) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "origin-session", "other-desktop")
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=_desktop_presence(),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    loaded = session_store.load("origin-session")
    reminder = next(event for event in loaded.events if event.type == "todo_reminder")
    assert reminder.uuid == occurrence.id
    assert reminder.payload["destination_kind"] == "conversation"
    assert reminder.payload["presentation"] == "conversation"
    assert runtime.submitted[0].session_id == "origin-session"
    assert runtime.submitted[0].presentation == "conversation"
    assert todo_store.get_occurrence(occurrence.id).persistence_reason == "origin_session"
    assert not any(
        event.type == "todo_reminder" for event in session_store.load("other-desktop").events
    )


def test_same_session_batch_submits_one_structured_agent_turn(tmp_path: Path) -> None:
    todo_store, occurrences = _claimed_many(tmp_path, ("提交材料", "预约体检"))
    session_store = _sessions(tmp_path, "origin-session")
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        clock=lambda: NOW,
    )

    orchestrator.deliver_batch(occurrences)

    assert len(runtime.submitted) == 1
    trigger = runtime.submitted[0]
    serialized = trigger.system_prompt_addendum.removeprefix("<system_events>\n").removesuffix(
        "\n</system_events>"
    )
    events = json.loads(serialized)
    assert [event["payload"]["occurrence_id"] for event in events] == [
        occurrence.id for occurrence in occurrences
    ]
    assert trigger.tool_mode == "normal"
    assert trigger.presentation == "conversation"
    assert str(runtime.submit_kwargs[0]["surface"]) == "desktop_chat"


def test_pet_delegation_keeps_agent_and_im_contract_and_does_not_replay_old_send(
    tmp_path: Path,
) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    sessions = _sessions(tmp_path, "origin-session")
    runtime = FakeAgentRuntime(subscribers=1)
    delegated: list[str] = []

    def notification(value: ReminderOccurrence) -> str:
        delegated.append(value.id)
        return "notification-1"

    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=sessions,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        pet_notification=notification,
        clock=lambda: NOW,
    )
    orchestrator.deliver(occurrence)
    orchestrator.deliver(todo_store.get_occurrence(occurrence.id))
    assert delegated == [occurrence.id]
    assert runtime.listeners.ambient == []
    assert len(runtime.submitted) == 1
    assert runtime.submitted[0].tool_mode == "normal"
    projection = todo_store.get_delivery(occurrence.id, ReminderProjectionKind.PET_FIXED)
    assert projection.state is ReminderDeliveryState.SKIPPED
    assert projection.error_kind == "delegated_to_notification_manager"
    assert projection.receipt == "notification-1"


def test_latest_session_user_interaction_owns_history_even_when_binding_recency_disagrees(
    tmp_path: Path,
) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "origin-session", "im-b", "im-c")
    _append_user_message(session_store, "im-b", at=NOW - timedelta(minutes=1))
    _append_user_message(session_store, "im-c", at=NOW - timedelta(minutes=10))
    b = _im(
        "im-b",
        bind="b",
        online=False,
        last_interaction_at=NOW - timedelta(minutes=30),
        binding_updated_at=NOW - timedelta(days=1),
    )
    c = _im(
        "im-c",
        bind="c",
        last_interaction_at=NOW,
        binding_updated_at=NOW,
    )
    im_runtime = FakeIMRuntime((b, c), outcome="failed", error_kind="provider_unavailable")
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        notification_mode_provider=lambda: ReminderNotificationMode.ALL,
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.persistence_session_id == "im-b"
    assert saved.persistence_destination_id == b.destination_id
    assert saved.persistence_reason == "latest_session_user_interaction"
    assert any(event.type == "todo_reminder" for event in session_store.load("im-b").events)
    assert not any(event.type == "todo_reminder" for event in session_store.load("im-c").events)
    assert not any(
        event.type == "todo_reminder" for event in session_store.load("origin-session").events
    )
    assert runtime.submitted[0].session_id == "im-b"
    assert str(runtime.submit_kwargs[0]["surface"]) == "im:qq"


def test_existing_session_history_repairs_missing_binding_interaction_data(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "im-b", "im-c")
    _append_user_message(session_store, "im-b", at=NOW - timedelta(minutes=1))
    _append_user_message(session_store, "im-c", at=NOW - timedelta(days=1))
    b = _im("im-b", bind="b", binding_updated_at=NOW - timedelta(days=2))
    c = _im("im-c", bind="c", binding_updated_at=NOW)
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, FakeIMRuntime((b, c))),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.persistence_session_id == "im-b"
    assert saved.persistence_reason == "latest_session_user_interaction"


def test_origin_bound_session_is_deterministic_fallback_without_user_history(
    tmp_path: Path,
) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "origin-session", "im-c")
    origin = _im(
        "origin-session",
        bind="origin",
        binding_updated_at=NOW - timedelta(days=2),
    )
    c = _im("im-c", bind="c", binding_updated_at=NOW)
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, FakeIMRuntime((origin, c))),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.persistence_session_id == "origin-session"
    assert saved.persistence_destination_id == origin.destination_id
    assert saved.persistence_reason == "origin_im_session_without_user_interaction"


def test_agent_and_reminder_outputs_do_not_change_session_activity(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "im-b", "im-c")
    _append_user_message(session_store, "im-b", at=NOW - timedelta(minutes=20))
    _append_user_message(session_store, "im-c", at=NOW - timedelta(minutes=10))
    _append_system_output(
        session_store,
        "im-b",
        event_type="assistant_message",
        at=NOW - timedelta(minutes=1),
    )
    _append_system_output(
        session_store,
        "im-b",
        event_type="todo_reminder",
        at=NOW,
    )
    b = _im("im-b", bind="b", last_interaction_at=NOW)
    c = _im("im-c", bind="c", last_interaction_at=NOW - timedelta(days=1))
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, FakeIMRuntime((b, c))),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.persistence_session_id == "im-c"
    assert saved.persistence_reason == "latest_session_user_interaction"


def test_new_occurrence_reselects_after_another_session_gets_a_user_message(
    tmp_path: Path,
) -> None:
    todo_store = TodoStore(tmp_path / "todos.db")
    service = TodoService(todo_store, clock=lambda: NOW - timedelta(minutes=5))
    todo = service.create(title="每日检查")
    service.add_reminder(
        todo.todo.id,
        schedule=RecurringSchedule(
            frequency="daily",
            interval=1,
            local_time=datetime_time(1, 5),
            timezone="UTC",
            starts_on=date(2026, 7, 28),
        ),
        origin_session_id="origin-session",
    )
    todo_store.create_due_occurrences(NOW)
    first = todo_store.claim_pending(NOW)[0]

    session_store = _sessions(tmp_path, "origin-session", "im-b", "im-c")
    _append_user_message(session_store, "im-b", at=NOW - timedelta(minutes=1))
    _append_user_message(session_store, "im-c", at=NOW - timedelta(minutes=10))
    b = _im("im-b", bind="b", last_interaction_at=NOW - timedelta(minutes=1))
    c = _im("im-c", bind="c", last_interaction_at=NOW - timedelta(minutes=10))
    im_runtime = FakeIMRuntime((b, c))
    runtime = FakeAgentRuntime()
    clock = [NOW]
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: clock[0],
    )

    orchestrator.deliver(first)
    assert todo_store.get_occurrence(first.id).persistence_session_id == "im-b"

    next_fire = NOW + timedelta(days=1)
    _append_user_message(session_store, "im-c", at=next_fire - timedelta(minutes=1))
    todo_store.create_due_occurrences(next_fire)
    second = todo_store.claim_pending(next_fire)[0]
    clock[0] = next_fire
    orchestrator.deliver(second)

    assert second.id != first.id
    assert todo_store.get_occurrence(first.id).persistence_session_id == "im-b"
    saved_second = todo_store.get_occurrence(second.id)
    assert saved_second.persistence_session_id == "im-c"
    assert saved_second.persistence_destination_id == c.destination_id
    assert saved_second.persistence_reason == "latest_session_user_interaction"
    assert runtime.submitted[0].session_id == "im-b"
    assert runtime.submitted[1].session_id == "im-c"


def test_smart_desktop_presence_suppresses_extra_notifications_but_not_im_history(
    tmp_path: Path,
) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "im-session", "other-desktop")
    im = _im(
        "im-session",
        bind="primary",
        last_interaction_at=NOW - timedelta(minutes=1),
        binding_updated_at=NOW - timedelta(days=1),
    )
    im_runtime = FakeIMRuntime((im,))
    runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=_desktop_presence(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.persistence_session_id == "im-session"
    assert saved.notification_mode is ReminderNotificationMode.SMART
    assert saved.notification_decision_kind == "desktop"
    assert todo_store.list_deliveries(occurrence.id) == ()
    assert im_runtime.sent == []
    assert runtime.listeners.ambient == []
    assert runtime.submitted[0].presentation == "conversation"


def test_all_mode_sends_selected_qq_and_pet_even_when_desktop_is_focused(
    tmp_path: Path,
) -> None:
    todo_store, occurrences = _claimed_many(tmp_path, ("提交材料", "预约体检"))
    session_store = _sessions(tmp_path, "im-session", "other-desktop")
    im = _im(
        "im-session",
        bind="primary",
        last_interaction_at=NOW,
        binding_updated_at=NOW,
    )
    im_runtime = FakeIMRuntime((im,))
    runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=_desktop_presence(),
        im_runtime=cast(Any, im_runtime),
        notification_mode_provider=lambda: ReminderNotificationMode.ALL,
        clock=lambda: NOW,
    )

    orchestrator.deliver_batch(occurrences)

    assert im_runtime.response_sent.wait(timeout=1)
    _wait_status(todo_store, tuple(item.id for item in occurrences), "completed")
    assert len(runtime.submitted) == 1
    assert runtime.submitted[0].presentation == "conversation"
    assert len(im_runtime.sent) == 3
    assert [item[2] for item in im_runtime.sent] == ["plain_text", "plain_text", "markdown"]
    assert len(runtime.listeners.ambient) == 2
    for occurrence in occurrences:
        deliveries = {
            item.projection_kind: item for item in todo_store.list_deliveries(occurrence.id)
        }
        assert deliveries[ReminderProjectionKind.IM_FIXED].state is ReminderDeliveryState.DELIVERED
        assert deliveries[ReminderProjectionKind.IM_AGENT].state is ReminderDeliveryState.DELIVERED
        assert deliveries[ReminderProjectionKind.PET_FIXED].state is ReminderDeliveryState.DELIVERED


def test_im_unknown_is_recorded_without_pet_fallback_or_agent_message(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "im-session")
    im = _im("im-session", bind="primary", last_interaction_at=NOW)
    im_runtime = FakeIMRuntime((im,), outcome="unknown", error_kind="send_timeout")
    runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)
    _wait_status(todo_store, (occurrence.id,), "completed")

    deliveries = {item.projection_kind: item for item in todo_store.list_deliveries(occurrence.id)}
    assert deliveries[ReminderProjectionKind.IM_FIXED].state is ReminderDeliveryState.UNKNOWN
    assert deliveries[ReminderProjectionKind.IM_AGENT].state is ReminderDeliveryState.SKIPPED
    assert runtime.listeners.ambient == []
    assert len(im_runtime.sent) == 1
    assert todo_store.get_occurrence(occurrence.id).state.value == "delivered"


def test_binding_changed_does_not_retarget_another_im_binding(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "im-b", "im-c")
    b = _im("im-b", bind="b", last_interaction_at=NOW - timedelta(hours=1))
    c = _im("im-c", bind="c", last_interaction_at=NOW)
    im_runtime = FakeIMRuntime((b, c), outcome="failed", error_kind="binding_changed")
    runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        notification_mode_provider=lambda: ReminderNotificationMode.ALL,
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)
    _wait_status(todo_store, (occurrence.id,), "completed")

    assert [item[0] for item in im_runtime.sent] == [c.destination_id]
    fixed = todo_store.get_delivery(occurrence.id, ReminderProjectionKind.IM_FIXED)
    assert fixed.state is ReminderDeliveryState.FAILED
    assert fixed.error_kind == "binding_changed"
    assert todo_store.get_occurrence(occurrence.id).persistence_session_id == "im-c"


def test_missing_binding_and_origin_does_not_write_an_arbitrary_session(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path, origin_session_id=None)
    session_store = _sessions(tmp_path, "arbitrary")
    runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=_desktop_presence("arbitrary"),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    assert todo_store.get_occurrence(occurrence.id).state.value == "failed"
    assert not any(
        event.type == "todo_reminder" for event in session_store.load("arbitrary").events
    )
    assert runtime.submitted == []
    assert runtime.listeners.ambient == []


def test_empty_agent_output_keeps_card_and_marks_agent_failed(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = _sessions(tmp_path, "origin-session")
    runtime = FakeAgentRuntime(response_text="")
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)
    _wait_status(todo_store, (occurrence.id,), "failed")

    assert any(
        event.type == "todo_reminder" for event in session_store.load("origin-session").events
    )


def test_cancelled_claim_is_not_persisted_or_notified(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    todo = todo_store.get_todo(occurrence.todo_id).todo
    TodoService(todo_store, clock=lambda: NOW).cancel(todo.id, revision=todo.revision)
    session_store = _sessions(tmp_path, "origin-session")
    runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, runtime),
        presence_registry=PresenceRegistry(),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    assert not any(
        event.type == "todo_reminder" for event in session_store.load("origin-session").events
    )
    assert runtime.listeners.ambient == []
    assert runtime.submitted == []
