"""Read-only pairing of effective business tool events; never replay an operation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ....sessions.events import Event
from ....sessions.reading import EventFilter, SessionRead

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
    def __init__(self, read: SessionRead, check: Callable[[], None]) -> None:
        self.read = read
        self.check = check
        marker = read.latest("turn_rewrite", active=False)
        self.rewrite = marker.uuid if marker else "initial"
        self._results: dict[str, tuple[Event | None, bool]] = {}

    def call(self, ref: str) -> tuple[int, Event] | None:
        rows = self.read.events(
            EventFilter(
                ids=(ref,), kinds=("tool_call_request",), exclude_tools=tuple(RECOVERY_TOOLS)
            ),
            limit=1,
        )
        return rows[0] if rows else None

    def calls_before(self, before: int | None, limit: int = 100) -> tuple[tuple[int, Event], ...]:
        return self.read.events(
            EventFilter(
                kinds=("tool_call_request",), before=before, exclude_tools=tuple(RECOVERY_TOOLS)
            ),
            limit=limit,
            reverse=True,
        )

    def result(self, call: Event) -> tuple[Event | None, bool]:
        if call.uuid in self._results:
            return self._results[call.uuid]
        result = self._result(call)
        self._results[call.uuid] = result
        return result

    def _result(self, call: Event) -> tuple[Event | None, bool]:
        for field in ("event_tool_call_id", "tool_call_id"):
            value = call.payload.get(field)
            if not isinstance(value, str) or not value:
                continue
            # Explicit IDs are session-wide; provider IDs are run-local. Select
            # only this association, preserving duplicate-request/result conflicts.
            selection = EventFilter(
                event_tool_call_id=value if field == "event_tool_call_id" else None,
                tool_call_id=value if field == "tool_call_id" else None,
                kinds=("tool_call_request", "tool_call_result"),
            )
            requests = 0
            matches = []
            for _, event in self.read.scan(selection, purpose="tool association"):
                self.check()
                if field == "tool_call_id" and _key(event, field, value) != _key(
                    call, field, value
                ):
                    continue
                if (
                    event.type == "tool_call_request"
                    and event.payload.get("tool_name") not in RECOVERY_TOOLS
                ):
                    requests += 1
                elif event.type == "tool_call_result" and event.payload.get(
                    "tool_name"
                ) == call.payload.get("tool_name"):
                    matches.append(event)
                if requests > 1 or len(matches) > 1:
                    return None, True
            if requests != 1:
                return None, True
            if matches:
                return matches[0], False
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
