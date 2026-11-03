"""Bounded business-message reads shared by tools and companionship snapshots."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .events import Event
from .query import CancelCheck, check_cancel
from .store_types import SourceVersion


@dataclass(frozen=True)
class MessagePage:
    session_id: str
    epoch: str
    upper: int
    messages: tuple[tuple[int, Event], ...]
    has_more: bool
    counts: dict[str, int]


# Business messages only: never mistake a synthetic user input or a hidden
# system turn for fresh human activity. Filtering happens before LIMIT/counts.
_VISIBLE = """e.active=1 AND e.kind IN ('user_message','assistant_message')
AND COALESCE(json_extract(e.body,'$.payload.partial'),0)=0
AND NOT (e.kind='user_message'
    AND json_extract(e.body,'$.payload.request_source.kind') IS NOT NULL)
AND NOT EXISTS (
    SELECT 1 FROM events t WHERE t.active=1 AND t.kind='system_trigger'
    AND json_extract(t.body,'$.meta.run_id') IS NOT NULL
    AND json_extract(t.body,'$.meta.run_id')=json_extract(e.body,'$.meta.run_id')
    AND COALESCE(json_extract(t.body,'$.payload.presentation'),'ambient')!='conversation'
)"""


def read_message_page(
    db: sqlite3.Connection,
    version: SourceVersion,
    *,
    limit: int = 20,
    lower: int = -1,
    upper: int | None = None,
    epoch: str | None = None,
    forward: bool = False,
    cancel_check: CancelCheck = None,
) -> MessagePage:
    """Read at most limit messages in a fixed raw-event interval (lower, upper)."""
    if not 1 <= limit <= 100 or lower < -1:
        raise ValueError("invalid message range")
    check_cancel(cancel_check)
    state = db.execute("SELECT * FROM state WHERE id=1").fetchone()
    if state is None or tuple(json.loads(state["version"])) != version:
        raise ValueError("history_changed")
    metadata = json.loads(state["metadata"])
    current_epoch = str(metadata["history_epoch"])
    if epoch is not None and epoch != current_epoch:
        raise ValueError("history_changed")
    end = state["count"] if upper is None else upper
    if end < 0 or end > state["count"] or lower >= end:
        raise ValueError("invalid message range")
    where = f"{_VISIBLE} AND e.position>? AND e.position<?"
    counts = {"user": 0, "assistant": 0, "proactive": 0}
    for row in db.execute(
        f"SELECT e.kind, COUNT(*) AS n FROM events e WHERE {where} GROUP BY e.kind",
        (lower, end),
    ):
        counts["user" if row["kind"] == "user_message" else "assistant"] = row["n"]
    counts["proactive"] = db.execute(
        f"SELECT COUNT(*) FROM events e WHERE {where} AND e.kind='assistant_message' "
        "AND json_extract(e.body,'$.meta.message_source.kind')='agent_initiated'",
        (lower, end),
    ).fetchone()[0]
    order = "ASC" if forward else "DESC"
    rows = db.execute(
        f"SELECT e.position,e.body FROM events e WHERE {where} ORDER BY e.position {order} LIMIT ?",
        (lower, end, limit + 1),
    ).fetchall()
    selected = rows[:limit]
    if not forward:
        selected.reverse()
    messages = tuple((r["position"], Event.from_jsonl(r["body"])) for r in selected)
    check_cancel(cancel_check)
    return MessagePage(
        str(metadata["session_id"]), current_epoch, end, messages, len(rows) > limit, counts
    )


def message_payload(position: int, event: Event, *, max_chars: int = 4000) -> dict[str, Any]:
    content = event.payload.get("content", "")
    text = content if isinstance(content, str) else ""
    return {
        "message_id": event.uuid,
        "position": position,
        "speaker": "user" if event.type == "user_message" else "assistant",
        "occurred_at": event.ts.isoformat(),
        "text": text[:max_chars],
        "content_truncated": len(text) > max_chars,
        "attachments": event.payload.get("attachments", []),
        "source": event.meta.get("message_source", {"kind": "conversation"}),
    }
