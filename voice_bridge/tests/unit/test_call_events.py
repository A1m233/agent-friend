"""Voice call event hub unit tests."""

from __future__ import annotations

import pytest
from voice_bridge.calls import VoiceCallEvent, VoiceCallEventHub


@pytest.mark.asyncio
async def test_event_hub_fans_out_by_call_id() -> None:
    hub = VoiceCallEventHub()
    queue_a = hub.subscribe("call-a")
    queue_b = hub.subscribe("call-b")

    hub.publish(VoiceCallEvent(type="callback", call_id="call-a", stage="thinking"))

    assert (await queue_a.get()).stage == "thinking"
    assert queue_b.empty()


@pytest.mark.asyncio
async def test_event_hub_unsubscribe_stops_delivery() -> None:
    hub = VoiceCallEventHub()
    queue = hub.subscribe("call-a")
    hub.unsubscribe("call-a", queue)

    hub.publish(VoiceCallEvent(type="callback", call_id="call-a", stage="answering"))

    assert queue.empty()
