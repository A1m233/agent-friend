from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agent.conversation_events import TextDelta
from agent.runtime import AgentRuntime, RunOutcome
from agent.todos import OnceSchedule, TodoService, TodoStore
from agent_bridge.presence import (
    DesktopPresenceSnapshot,
    DestinationDecision,
    IMPresenceSnapshot,
    PresenceRegistry,
)
from agent_bridge.protocols.im.content import OutboundTextFormat
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.reminders import ReminderDeliveryOrchestrator

from agent import JsonlSessionStore, Session

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
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.sent: list[str] = []
        self.sent_formats: list[OutboundTextFormat] = []
        self.response_sent = threading.Event()
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

    def send_proactive(
        self,
        _destination_id: str,
        text: str,
        *,
        text_format: OutboundTextFormat = "plain_text",
    ) -> OutboundSendResult:
        self.sent.append(text)
        self.sent_formats.append(text_format)
        if len(self.sent) >= 3:
            self.response_sent.set()
        return OutboundSendResult(cast(Any, self.outcome), error_kind="test")


class SequencePolicy:
    def __init__(self, decisions: tuple[DestinationDecision, ...]) -> None:
        self._decisions = iter(decisions)

    def decide(self, _state):  # type: ignore[no-untyped-def]
        return next(self._decisions)


def _claimed(tmp_path: Path):  # type: ignore[no-untyped-def]
    store, occurrences = _claimed_many(tmp_path, ("提交材料",))
    return store, occurrences[0]


def _claimed_many(tmp_path: Path, titles: tuple[str, ...]):  # type: ignore[no-untyped-def]
    store = TodoStore(tmp_path / "todos.db")
    service = TodoService(store, clock=lambda: datetime(2026, 7, 28, 1, tzinfo=UTC))
    for title in titles:
        todo = service.create(title=title, description="带齐附件")
        service.add_reminder(
            todo.todo.id,
            schedule=OnceSchedule(datetime(2026, 7, 28, 9, 1), "Asia/Shanghai"),
        )
    store.create_due_occurrences(NOW)
    return store, tuple(store.claim_pending(NOW))


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
    assert loaded.events[-1].payload["occurrence_status"] == "delivered"
    assert loaded.events[-1].payload["presentation"] == "conversation"
    assert agent_runtime.submitted[0].presentation == "conversation"
    assert todo_store.get_occurrence(occurrence.id).state.value == "delivered"


def test_desktop_batch_submits_one_structured_tool_enabled_agent_turn(
    tmp_path: Path,
) -> None:
    todo_store, occurrences = _claimed_many(tmp_path, ("提交材料", "预约体检"))
    session_store = JsonlSessionStore(tmp_path / "sessions")
    session_store.create(
        Session.new(title="chat", persona="default", model="fake", session_id="desktop")
    )
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

    orchestrator.deliver_batch(occurrences)

    assert len(agent_runtime.submitted) == 1
    trigger = agent_runtime.submitted[0]
    assert trigger.tool_mode == "normal"
    assert trigger.presentation == "conversation"
    assert trigger.system_prompt_addendum.startswith("<system_events>\n")
    assert trigger.system_prompt_addendum.endswith("\n</system_events>")
    serialized = trigger.system_prompt_addendum.removeprefix("<system_events>\n").removesuffix(
        "\n</system_events>"
    )
    events = json.loads(serialized)
    assert [event["type"] for event in events] == ["todo_reminder", "todo_reminder"]
    assert [event["payload"]["occurrence_id"] for event in events] == [
        occurrence.id for occurrence in occurrences
    ]
    assert "不要调用工具" not in trigger.system_prompt_addendum
    assert "请结合" not in trigger.system_prompt_addendum
    assert trigger.event_metadata["occurrence_ids"] == [occurrence.id for occurrence in occurrences]
    assert agent_runtime.submit_kwargs[0]["channel"] == "text"
    assert str(agent_runtime.submit_kwargs[0]["surface"]) == "desktop_chat"
    assert agent_runtime.submit_kwargs[0]["metadata"]["interaction_destination_id"] == ("desktop")
    loaded = session_store.load("desktop")
    reminder_events = [event for event in loaded.events if event.type == "todo_reminder"]
    assert [event.uuid for event in reminder_events] == [
        occurrence.id for occurrence in occurrences
    ]


def test_empty_agent_output_marks_all_grouped_occurrences_failed(tmp_path: Path) -> None:
    todo_store, occurrences = _claimed_many(tmp_path, ("提交材料", "预约体检"))
    session_store = JsonlSessionStore(tmp_path / "sessions")
    session_store.create(
        Session.new(title="chat", persona="default", model="fake", session_id="desktop")
    )
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
        )
    )
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, FakeAgentRuntime(response_text="")),
        presence_registry=presence,
        clock=lambda: NOW,
    )

    orchestrator.deliver_batch(occurrences)

    deadline = time.monotonic() + 1
    statuses: list[str] = []
    while time.monotonic() < deadline:
        statuses = [
            todo_store.get_occurrence(occurrence.id).agent_response_status.value
            for occurrence in occurrences
        ]
        if statuses == ["failed", "failed"]:
            break
        time.sleep(0.01)
    assert statuses == ["failed", "failed"]


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
    session_store = JsonlSessionStore(tmp_path / "sessions")
    session_store.create(
        Session.new(title="im", persona="default", model="fake", session_id="im-session")
    )
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: NOW,
    )

    orchestrator.deliver(occurrence)

    loaded = session_store.load("im-session")
    reminder_event = next(event for event in loaded.events if event.type == "todo_reminder")
    assert reminder_event.payload["occurrence_status"] == "delivered"
    assert reminder_event.payload["destination_kind"] == "im"
    assert reminder_event.payload["presentation"] == "none"
    assert agent_runtime.submitted[0].presentation == "none"


def test_im_batch_sends_each_fixed_fact_then_one_agent_response(tmp_path: Path) -> None:
    todo_store, occurrences = _claimed_many(tmp_path, ("提交材料", "预约体检"))
    agent_runtime = FakeAgentRuntime(subscribers=1)
    im_runtime = FakeIMRuntime("delivered")
    session_store = JsonlSessionStore(tmp_path / "sessions")
    session_store.create(
        Session.new(title="im", persona="default", model="fake", session_id="im-session")
    )
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=PresenceRegistry(),
        im_runtime=cast(Any, im_runtime),
        clock=lambda: NOW,
    )

    orchestrator.deliver_batch(occurrences)

    assert im_runtime.response_sent.wait(timeout=1)
    assert len(agent_runtime.submitted) == 1
    assert agent_runtime.submitted[0].presentation == "none"
    assert str(agent_runtime.submit_kwargs[0]["surface"]) == "im:qq"
    assert len(im_runtime.sent) == 3
    assert im_runtime.sent[-1] == "已提醒。"
    assert im_runtime.sent_formats == ["plain_text", "plain_text", "markdown"]
    deadline = time.monotonic() + 1
    statuses: list[str] = []
    while time.monotonic() < deadline:
        statuses = [
            todo_store.get_occurrence(occurrence.id).agent_response_status.value
            for occurrence in occurrences
        ]
        if statuses == ["completed", "completed"]:
            break
        time.sleep(0.01)
    assert statuses == ["completed", "completed"]


def test_batch_keeps_different_actual_targets_in_separate_agent_turns(tmp_path: Path) -> None:
    todo_store, occurrences = _claimed_many(tmp_path, ("提交材料", "预约体检"))
    agent_runtime = FakeAgentRuntime(subscribers=1)
    im_runtime = FakeIMRuntime("delivered")
    session_store = JsonlSessionStore(tmp_path / "sessions")
    for session_id in ("desktop", "im-session"):
        session_store.create(
            Session.new(title=session_id, persona="default", model="fake", session_id=session_id)
        )
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
        )
    )
    policy = SequencePolicy(
        (
            DestinationDecision("desktop", "desktop", "desktop", "test-desktop"),
            DestinationDecision(
                "im",
                im_runtime.presence.destination_id,
                "im-session",
                "test-im",
            ),
        )
    )
    orchestrator = ReminderDeliveryOrchestrator(
        todo_store=todo_store,
        session_store=session_store,
        agent_runtime=cast(AgentRuntime, agent_runtime),
        presence_registry=presence,
        im_runtime=cast(Any, im_runtime),
        policy=cast(Any, policy),
        clock=lambda: NOW,
    )

    orchestrator.deliver_batch(occurrences)

    assert len(agent_runtime.submitted) == 2
    assert [event.session_id for event in agent_runtime.submitted] == ["desktop", "im-session"]
    assert [len(event.event_metadata["occurrence_ids"]) for event in agent_runtime.submitted] == [
        1,
        1,
    ]
    assert len({event.event_metadata["batch_id"] for event in agent_runtime.submitted}) == 1


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
