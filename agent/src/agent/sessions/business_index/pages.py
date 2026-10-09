"""Select complete turns from the business index without reading other bodies."""

from __future__ import annotations

import json
import sqlite3

from llm_providers import ModelRuntimeConfig

from ...errors import AgentError
from ..continuation import continuation_target
from ..events import Event
from ..query import CancelCheck, SessionHistoryPage, check_cancel
from ..store_types import SourceVersion

_RECOVERY_TYPES = {
    "user_message",
    "system_trigger",
    "run_started",
    "run_terminal",
    "run_error",
    "tool_call_request",
    "tool_call_result",
}
_RECOVERY_FIELDS = {
    "run_id",
    "root_run_id",
    "status",
    "recoverable",
    "event_tool_call_id",
    "tool_call_id",
}


def recovery_body(event: Event) -> str | None:
    """Only facts consumed by continuation_target; never duplicate message bodies."""
    if event.type not in _RECOVERY_TYPES:
        return None
    return Event(
        type=event.type,
        uuid=event.uuid,
        ts=event.ts,
        payload={key: value for key, value in event.payload.items() if key in _RECOVERY_FIELDS},
        meta={"run_id": event.meta["run_id"]} if "run_id" in event.meta else {},
    ).to_jsonl()


def page(
    db: sqlite3.Connection,
    version: SourceVersion,
    *,
    turn_limit: int,
    before: str | None = None,
    history_epoch: str | None = None,
    cancel_check: CancelCheck = None,
) -> SessionHistoryPage:
    if turn_limit < 1:
        raise ValueError("turn_limit must be positive")
    state = db.execute("SELECT * FROM state WHERE id=1").fetchone()
    if state is None or tuple(json.loads(state["version"])) != version:
        raise ValueError("History changed; reload the latest page")
    metadata = json.loads(state["metadata"])
    if history_epoch is not None and history_epoch != metadata["history_epoch"]:
        raise ValueError("History changed; reload the latest page")
    first = db.execute(
        "SELECT position,uuid FROM events WHERE active=1 ORDER BY position LIMIT 1"
    ).fetchone()
    end = state["count"]
    if before is not None:
        boundary = db.execute(
            "SELECT position,kind FROM events WHERE active=1 AND uuid=?", (before,)
        ).fetchone()
        if boundary is None:
            raise ValueError("history cursor is no longer available")
        end = boundary["position"]
        first_turn = db.execute(
            "SELECT position FROM events WHERE active=1 AND kind IN ('user_message','system_trigger') "
            "ORDER BY position LIMIT 1"
        ).fetchone()
        if (
            first is not None
            and end != first["position"]
            and (
                boundary["kind"] not in {"user_message", "system_trigger"}
                or (first_turn is not None and end == first_turn["position"])
            )
        ):
            raise ValueError("history cursor must point to a complete turn boundary")
    starts = db.execute(
        "SELECT position,uuid FROM events WHERE active=1 "
        "AND kind IN ('user_message','system_trigger') AND position<? "
        "ORDER BY position DESC LIMIT ?",
        (end, turn_limit + 1),
    ).fetchall()
    # As in SessionQuerySnapshot.page_events, metadata before the first input
    # belongs to that first turn. Never introduce a separate metadata page.
    start = starts[turn_limit - 1] if len(starts) > turn_limit else first
    cursor = start["uuid"] if len(starts) > turn_limit else None
    events = []
    if start is not None:
        for row in db.execute(
            "SELECT body FROM events WHERE active=1 AND position>=? AND position<? ORDER BY position",
            (start["position"], end),
        ):
            check_cancel(cancel_check)
            events.append(Event.from_jsonl(row["body"]))
    latest_task = db.execute(
        "SELECT position FROM events WHERE active=1 AND kind IN ('user_message','system_trigger') "
        "ORDER BY position DESC LIMIT 1"
    ).fetchone()
    facts = []
    if latest_task is not None:
        for row in db.execute(
            "SELECT recovery FROM events WHERE active=1 AND position>=? "
            "AND recovery IS NOT NULL ORDER BY position",
            (latest_task["position"],),
        ):
            check_cancel(cancel_check)
            facts.append(Event.from_jsonl(row["recovery"]))
    try:
        recovery = continuation_target(facts)
    except AgentError:
        recovery = None
    check_cancel(cancel_check)
    return SessionHistoryPage(
        session_id=metadata["session_id"],
        initial_title=metadata["title"],
        current_persona=metadata["persona"],
        current_model=metadata["model"],
        current_model_config=ModelRuntimeConfig(metadata["model"], metadata["reasoningPreset"]),
        active_events=tuple(events),
        history_cursor=cursor,
        history_epoch=metadata["history_epoch"],
        recovery=recovery,
    )
