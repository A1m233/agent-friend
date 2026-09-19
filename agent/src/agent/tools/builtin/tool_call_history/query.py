"""Read-only pairing of effective business tool events; never replay an operation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ....sessions.events import Event
from ....sessions.query import SessionQuerySnapshot

RECOVERY_TOOLS = {"inspect_tool_calls", "recover_tool_result"}


def arguments_text(event: Event) -> str:
    args = event.payload.get("args")
    if (
        isinstance(args, dict)
        and event.payload.get("arguments_state", "valid_object") == "valid_object"
    ):
        args = {key: value for key, value in args.items() if not key.startswith("__agent_friend_")}
        return json.dumps(args, ensure_ascii=False, sort_keys=True)
    return str(event.payload.get("arguments_json", ""))


def _key(event: Event, field: str, value: str) -> tuple[str, str, str]:
    # Event identities are session-unique; legacy provider IDs are only run-local.
    return (
        "" if field == "event_tool_call_id" else str(event.meta.get("run_id", "")),
        field,
        value,
    )


class CallHistory:
    def __init__(self, snapshot: SessionQuerySnapshot, check: Callable[[], None]) -> None:
        self.calls: list[Event] = []
        self.by_ref: dict[str, Event] = {}
        self.requests: dict[tuple[str, str, str], list[Event]] = {}
        self.results: dict[tuple[str, str, str], list[Event]] = {}
        self.rewrite = "initial"
        for event in snapshot.events:
            check()
            if event.type == "turn_rewrite":
                self.rewrite = event.uuid
        for event in snapshot.active_events:
            check()
            if (
                event.type == "tool_call_request"
                and event.payload.get("tool_name") not in RECOVERY_TOOLS
            ):
                self.calls.append(event)
                self.by_ref[event.uuid] = event
                for field in ("event_tool_call_id", "tool_call_id"):
                    value = event.payload.get(field)
                    if isinstance(value, str) and value:
                        key = _key(event, field, value)
                        self.requests.setdefault(key, []).append(event)
            elif event.type == "tool_call_result":
                for field in ("event_tool_call_id", "tool_call_id"):
                    value = event.payload.get(field)
                    if isinstance(value, str) and value:
                        key = _key(event, field, value)
                        self.results.setdefault(key, []).append(event)

    def result(self, call: Event) -> tuple[Event | None, bool]:
        for field in ("event_tool_call_id", "tool_call_id"):
            value = call.payload.get(field)
            if not isinstance(value, str) or not value:
                continue
            key = _key(call, field, value)
            if len(self.requests.get(key, [])) != 1:
                return None, True
            matches = self.results.get(key, [])
            matches = [
                item
                for item in matches
                if item.payload.get("tool_name") == call.payload.get("tool_name")
            ]
            if len(matches) > 1:
                return None, True
            if matches:
                return matches[0], False
            # An explicit identity must not fall back to a reused provider ID.
            if field == "event_tool_call_id":
                return None, False
        return None, False

    def describe(self, call: Event) -> dict[str, Any]:
        result, ambiguous = self.result(call)
        return {
            "call_ref": call.uuid,
            "tool_name": call.payload.get("tool_name"),
            "timestamp": call.ts.isoformat(),
            "result_ref": result.uuid if result else None,
            "executed": result.payload.get("executed") if result else None,
            "is_error": result.payload.get("is_error") if result else None,
            "process_status": result.payload.get("process_status") if result else None,
            "semantic_status": result.payload.get("semantic_status") if result else None,
            "evidence_ref": result.payload.get("evidence_ref") if result else None,
            "state": "association_conflict"
            if ambiguous
            else "result_recorded"
            if result
            else "unknown",
            "arguments_preview": arguments_text(call)[:240],
            "arguments_state": call.payload.get("arguments_state", "valid_object"),
        }
