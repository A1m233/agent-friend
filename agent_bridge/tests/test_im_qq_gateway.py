from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_bridge.protocols.im.adapters.qq import QQAdapter
from agent_bridge.protocols.im.adapters.qq_gateway import DISPATCH_EVENT_ID, QQWebSocket
from agent_bridge.protocols.im.credentials import ImCredential


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_id", ["gateway-event-id", None])
async def test_gateway_dispatch_keeps_ack_and_reply_ids_distinct(
    tmp_path: Any, outer_id: str | None
) -> None:
    adapter = QQAdapter(
        cred=ImCredential("qq", "owner", "app", "secret", "owner"), resume_base_dir=tmp_path
    )
    api = MagicMock()
    api.acknowledge_interaction = AsyncMock()
    adapter._api = api
    forwarded = AsyncMock()
    adapter._on_interaction = forwarded
    callbacks = adapter._make_ws_callbacks(api, object())
    callbacks.get_session = lambda: ("session", None)
    callbacks.set_session = MagicMock()
    gateway = QQWebSocket(callbacks=callbacks)
    raw: dict[str, Any] = {
        "op": 0,
        "s": 9,
        "t": "INTERACTION_CREATE",
        "id": outer_id,
        "d": {
            "id": "ack-interaction-id",
            "type": 11,
            "chat_type": 2,
            "user_openid": "owner",
            DISPATCH_EVENT_ID: "untrusted-inner-id",
            "data": {"resolved": {"button_data": "fixture-token", "button_id": "button"}},
        },
    }
    scheduled: list[Any] = []
    with patch.object(gateway, "_dispatch_to_main", side_effect=scheduled.append):
        gateway._dispatch_payload(raw)
    assert len(scheduled) == 1
    await scheduled[0]
    api.acknowledge_interaction.assert_awaited_once_with("ack-interaction-id", code=0)
    assert forwarded.await_args is not None
    event = forwarded.await_args.args[0]
    assert event.interaction_id == "ack-interaction-id"
    assert event.reply_event_id == outer_id
    assert event.payload == "fixture-token"
    assert raw["d"][DISPATCH_EVENT_ID] == "untrusted-inner-id"
