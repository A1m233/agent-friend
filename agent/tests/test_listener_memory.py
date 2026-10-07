from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agent.runtime import Subscriber, UserEvent
from agent.runtime.listeners import ListenerRegistry, PushEnvelope
from agent.scheduled_tasks import ScheduledTaskError, ScheduledTaskService, ScheduledTaskStore
from agent.scheduling import OnceSchedule

from agent import TextDelta


@pytest.mark.asyncio
async def test_task_invalidation_follows_committed_management_changes(tmp_path: Path) -> None:
    registry = ListenerRegistry()
    sub = Subscriber(
        loop=asyncio.get_running_loop(), accept_kinds=frozenset({"scheduled_tasks_changed"})
    )
    registry.register(sub)
    now = datetime.now(UTC)
    service = ScheduledTaskService(ScheduledTaskStore(tmp_path / "tasks.db"), clock=lambda: now)
    service.add_wake_listener(registry.publish_scheduled_tasks_change)
    task = service.create(
        name="task",
        instruction="hello",
        target_session_id="s",
        schedule=OnceSchedule(
            local_datetime=(now + timedelta(hours=1)).replace(tzinfo=None), timezone="UTC"
        ),
        persona_name="default",
        model_id="fake/model",
    )
    await asyncio.sleep(0)
    assert (await sub.queue.get()).kind == "scheduled_tasks_changed"
    assert service.get(task.id) == task
    with pytest.raises(ScheduledTaskError):
        service.update(task.id, revision=0, name="conflict")
    await asyncio.sleep(0)
    assert sub.queue.empty()
    service.pause(task.id, revision=task.revision)
    await asyncio.sleep(0)
    assert (await sub.queue.get()).seq == 2


def test_pending_loop_callbacks_are_bounded_before_async_queue_runs() -> None:
    loop = asyncio.new_event_loop()
    try:
        registry = ListenerRegistry()
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"run_event"}), queue_maxsize=2)
        registry.register(sub)
        for index in range(10):
            registry._safe_push(sub, PushEnvelope("run_event", "s", index, None, []))
        assert sub.disconnected.is_set()
        assert sub.pending_count == 2
        assert registry.subscriber_count() == 0
        loop.run_until_complete(asyncio.sleep(0))
        assert sub.pending_count == 0
        assert sub.pending_bytes == 0
        assert sub.queue.empty()
    finally:
        loop.close()


def test_turn_buffer_overflow_disconnects_instead_of_advertising_partial_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.runtime.listeners as listeners

    monkeypatch.setattr(listeners, "_SUBSCRIBER_MAX_BYTES", 100)
    loop = asyncio.new_event_loop()
    try:
        registry = ListenerRegistry()
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"user_turn"}))
        registry.register(sub)
        event = UserEvent(session_id="s", user_input="hello")
        registry.fan_out_event(event, TextDelta(text="x" * 200), run_id="r")
        assert sub.disconnected.is_set()
        assert not registry._turn_buffers
        assert not registry._turn_buffer_bytes
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_consumed_queue_entries_release_byte_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.runtime.listeners as listeners

    monkeypatch.setattr(listeners, "_SUBSCRIBER_MAX_BYTES", 2000)
    sub = Subscriber(loop=asyncio.get_running_loop(), accept_kinds=frozenset())
    for index in range(20):
        envelope = PushEnvelope("heartbeat", "s", index, None, [])
        sub.queue.put_nowait(envelope)
        assert await sub.queue.get() is envelope


@pytest.mark.asyncio
async def test_repeated_envelope_entries_release_each_entries_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.runtime.listeners as listeners

    envelope = PushEnvelope("heartbeat", "s", 1, None, [])
    monkeypatch.setattr(listeners, "_SUBSCRIBER_MAX_BYTES", 2 * listeners._envelope_size(envelope))
    sub = Subscriber(loop=asyncio.get_running_loop(), accept_kinds=frozenset())
    for _ in range(3):
        sub.queue.put_nowait(envelope)
        sub.queue.put_nowait(envelope)
        with pytest.raises(asyncio.QueueFull):
            sub.queue.put_nowait(envelope)
        assert await sub.queue.get() is envelope
        assert await sub.queue.get() is envelope
