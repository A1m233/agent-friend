"""Persist exactly the event fields exposed by the chat history API."""

from dataclasses import replace
from typing import Any

from ..events import Event


def public_reasoning_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: payload[key]
        for key in ("provider", "model", "format", "replayScope", "dataCharCount", "detailCount")
        if key in payload
    }
    data = payload.get("data")
    if isinstance(data, str):
        public["dataCharCount"] = len(data)
    elif isinstance(data, list):
        public["detailCount"] = len(data)
    public["redacted"] = True
    return public


def display_event(event: Event) -> Event:
    meta = event.meta
    if "context_usage" in meta:
        meta = {key: value for key, value in meta.items() if key != "context_usage"}
    payload = event.payload
    if event.type == "provider_reasoning_state":
        payload = public_reasoning_payload(payload)
    if meta is event.meta and payload is event.payload:
        return event
    return replace(event, meta=meta, payload=payload)
