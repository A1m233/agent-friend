from __future__ import annotations

from agent.conversation_events import TextDelta
from agent.runtime import ListenerRegistry, UserEvent


def test_run_event_journal_records_user_run_without_subscriber() -> None:
    registry = ListenerRegistry(runtime_instance_id="runtime-1")

    registry.fan_out_event(
        UserEvent(session_id="session-1", user_input="hello"),
        TextDelta(text="first"),
        run_id="run-1",
    )

    snapshot = registry.run_event_journal("run-1")
    assert snapshot is not None
    assert snapshot.runtime_instance_id == "runtime-1"
    assert snapshot.first_seq == 1
    assert snapshot.next_seq == 2
    assert snapshot.events[0].event_seq == 1
    assert snapshot.events[0].event == {"type": "text_delta", "text": "first"}


def test_run_event_journal_reports_truncation_and_gap() -> None:
    registry = ListenerRegistry(
        runtime_instance_id="runtime-1",
        journal_max_events=2,
        journal_max_bytes=1024,
    )
    event = UserEvent(session_id="session-1", user_input="hello")
    for text in ("one", "two", "three"):
        registry.fan_out_event(event, TextDelta(text=text), run_id="run-1")

    snapshot = registry.run_event_journal("run-1", after_seq=0)

    assert snapshot is not None
    assert snapshot.truncated is True
    assert snapshot.gap_before_requested_seq is True
    assert snapshot.first_seq == 2
    assert snapshot.next_seq == 4
    assert [item.event_seq for item in snapshot.events] == [2, 3]


def test_run_event_journal_returns_defensive_event_copy() -> None:
    registry = ListenerRegistry(runtime_instance_id="runtime-1")
    registry.fan_out_event(
        UserEvent(session_id="session-1", user_input="hello"),
        TextDelta(text="safe"),
        run_id="run-1",
    )

    first = registry.run_event_journal("run-1")
    assert first is not None
    first.events[0].event["text"] = "mutated"

    second = registry.run_event_journal("run-1")
    assert second is not None
    assert second.events[0].event["text"] == "safe"
