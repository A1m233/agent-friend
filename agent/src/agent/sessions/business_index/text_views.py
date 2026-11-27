"""Small text/search facts, maintained with the business journal transaction."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from ..events import Event
from ..recall.snapshot import conversation_event

DDL = (
    "CREATE TABLE text_views (uuid TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, partial INTEGER NOT NULL, request_source INTEGER NOT NULL, run_id TEXT, hidden_trigger INTEGER NOT NULL, proactive INTEGER NOT NULL, recall INTEGER NOT NULL)",
    "CREATE INDEX text_run ON text_views(run_id,hidden_trigger)",
    "CREATE INDEX text_recall ON text_views(recall,uuid)",
)


def insert(db: sqlite3.Connection, event: Event) -> None:
    if event.type not in {
        "user_message",
        "assistant_message",
        "system_trigger",
        "todo_reminder",
        "tool_call_request",
        "run_error",
    }:
        return
    payload = event.payload
    fields = {
        "user_message": ("content", "attachments"),
        "assistant_message": ("content",),
        "system_trigger": ("output_visibility", "presentation", "source_kind"),
        "todo_reminder": ("presentation",),
    }.get(event.type, ())
    lean = replace(
        event,
        payload={key: payload[key] for key in fields if key in payload},
        meta={"run_id": event.meta["run_id"]} if "run_id" in event.meta else {},
    )
    request = payload.get("request_source")
    source = event.meta.get("message_source")
    db.execute(
        "INSERT INTO text_views VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event.uuid,
            event.type,
            lean.to_jsonl(),
            bool(payload.get("partial")),
            isinstance(request, dict) and request.get("kind") is not None,
            event.meta.get("run_id"),
            event.type == "system_trigger"
            and payload.get("presentation", "ambient") != "conversation",
            isinstance(source, dict) and source.get("kind") == "agent_initiated",
            conversation_event(event) is not None,
        ),
    )
