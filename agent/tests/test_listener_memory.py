from __future__ import annotations

import asyncio

import pytest
from agent.runtime import Subscriber, UserEvent
from agent.runtime.listeners import ListenerRegistry, PushEnvelope

from agent import TextDelta


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
