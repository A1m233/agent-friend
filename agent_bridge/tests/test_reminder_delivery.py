from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agent.runtime import AgentRuntime, RunOutcome
from agent.todos import OnceSchedule, TodoService, TodoStore
from agent_bridge.presence import DesktopPresenceSnapshot, IMPresenceSnapshot, PresenceRegistry
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.reminders import ReminderDeliveryOrchestrator

from agent import JsonlSessionStore, Session

NOW = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)


class FakeHandle:
    terminal = SimpleNamespace(outcome=RunOutcome.COMPLETED)

    def events(self):  # type: ignore[no-untyped-def]
        return iter(())


class FakeListeners:
    def __init__(self, subscribers: int) -> None:
        self.subscribers = subscribers
        self.ambient: list[dict[str, str]] = []

    def subscriber_count(self) -> int:
        return self.subscribers

    def publish_fixed_ambient(self, **payload: str) -> None:
        self.ambient.append(payload)


class FakeAgentRuntime:
    def __init__(self, subscribers: int = 0) -> None:
        self.listeners = FakeListeners(subscribers)
        self.submitted: list[Any] = []

    def submit(self, event, **_kwargs):  # type: ignore[no-untyped-def]
        self.submitted.append(event)
        return FakeHandle()


class FakeIMRuntime:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.sent: list[str] = []
        self.presence = IMPresenceSnapshot(
            im_type="qq",
            provider_bind_id="bind",
            chat_scope="c2c",
            chat_id="chat",
            session_id="im-session",
            online=True,
            last_interaction_at=NOW,
        )

    def presence_snapshots(self):  # type: ignore[no-untyped-def]
        return (self.presence,)

    def send_proactive(self, _destination_id: str, text: str) -> OutboundSendResult:
        self.sent.append(text)
        return OutboundSendResult(cast(Any, self.outcome), error_kind="test")


def _claimed(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    todo = service.create(title="提交材料", description="带齐附件")
    service.add_reminder(
        todo.todo.id,
        schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
    )
    store.create_due_occurrences(NOW)
    return store, store.claim_pending(NOW)[0]


def test_desktop_delivery_persists_fact_before_agent_response(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    session_store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="chat", persona="default", model="fake", session_id="desktop")
    session_store.create(session)
    presence = PresenceRegistry()
    presence.update_desktop(
        DesktopPresenceSnapshot(
            observed_at=NOW,
            current_session_id="desktop",
            window_visible=True,
            window_focused=True,
            window_minimized=False,
            os_idle_ms=100,
            session_state="active",
            last_desktop_interaction_at=NOW,
            capabilities=("idle", "lock", "window"),
        )
    )
    agent_runtime = FakeAgentRuntime()
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=presence,
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    loaded = session_store.load("desktop")
    assert [event.type for event in loaded.events][-1] == "todo_reminder"
    assert loaded.events[-1].uuid == occurrence.id
    assert agent_runtime.submitted[0].presentation == "conversation"
    assert todo_store.get_occurrence(occurrence.id).state.value == "delivered"


def test_pet_delivery_is_fixed_and_skips_agent(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    agent_runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=JsonlSessionStore(tmp_path / "sessions"),
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=PresenceRegistry(),
        clock=lambda: NOW,
    )
    orchestrator.deliver(occurrence)
    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.destination_kind == "pet"
    assert saved.agent_response_status.value == "skipped"
    assert agent_runtime.listeners.ambient[0]["occurrence_id"] == occurrence.id
    assert agent_runtime.submitted == []


def test_im_unknown_does_not_fall_back_to_pet(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    agent_runtime = FakeAgentRuntime(subscribers=1)
    im_runtime = FakeIMRuntime("unknown")
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=JsonlSessionStore(tmp_path / "sessions"),
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: NOW,
    )
    orchestrator.deliver(occurrence)
    saved = todo_store.get_occurrence(occurrence.id)
    assert saved.state.value == "delivery_unknown"
    assert saved.destination_kind == "im"
    assert agent_runtime.listeners.ambient == []


def test_im_delivery_keeps_agent_response_out_of_desktop_conversation(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    agent_runtime = FakeAgentRuntime(subscribers=1)
    im_runtime = FakeIMRuntime("delivered")
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=JsonlSessionStore(tmp_path / "sessions"),
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    assert agent_runtime.submitted[0].presentation == "none"


def test_cancelled_claim_is_not_sent_to_any_destination(tmp_path: Path) -> None:
    todo_store, occurrence = _claimed(tmp_path)
    todo = todo_store.get_todo(occurrence.todo_id).todo
    TodoService(todo_store, clock=lambda: NOW).cancel(todo.id, revision=todo.revision)
    agent_runtime = FakeAgentRuntime(subscribers=1)
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=JsonlSessionStore(tmp_path / "sessions"),
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=PresenceRegistry(),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    assert agent_runtime.listeners.ambient == []
    assert agent_runtime.submitted == []
