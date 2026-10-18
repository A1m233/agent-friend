"""Preserve dispatch reply identity discarded by the pinned QQ SDK."""

from __future__ import annotations

from typing import Any

from qqbot_agent_sdk.websocket import QQWebSocket as SDKWebSocket

DISPATCH_EVENT_ID = "_af_dispatch_event_id"


class QQWebSocket(SDKWebSocket):
    def _dispatch_payload(self, raw: dict[str, Any]) -> None:
        # d.id identifies the interaction for ACK; outer id identifies the event
        # for a message reply. Keep both without changing the SDK parser or ACK.
        if raw.get("op") == 0 and raw.get("t") == "INTERACTION_CREATE":
            data = raw.get("d")
            if isinstance(data, dict):
                data = dict(data)
                data.pop(DISPATCH_EVENT_ID, None)
                event_id = raw.get("id")
                if isinstance(event_id, str) and event_id:
                    data[DISPATCH_EVENT_ID] = event_id
                raw = {**raw, "d": data}
        super()._dispatch_payload(raw)
