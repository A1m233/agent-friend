"""Persisted business changes; attribution is supplied by trusted entry points."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TodoActor:
    kind: Literal["user", "agent", "system", "unknown"] = "unknown"
    session_id: str | None = None


UNKNOWN_ACTOR = TodoActor()


def snapshot(conn: sqlite3.Connection, todo_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT title, description, status FROM todos WHERE id = ?", (todo_id,)
    ).fetchone()
    if row is None:
        return {}
    reminders = conn.execute(
        "SELECT id, schedule_json, status FROM reminders WHERE todo_id = ? ORDER BY id", (todo_id,)
    ).fetchall()
    return {
        **dict(row),
        "reminders": {
            item["id"]: {"schedule": json.loads(item["schedule_json"]), "status": item["status"]}
            for item in reminders
        },
    }


def append_change(
    conn: sqlite3.Connection,
    *,
    todo_id: str,
    action: str,
    actor: TodoActor,
    occurred_at: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    if not changed:
        return
    conn.execute(
        """INSERT INTO todo_history
        (todo_id, action, actor_kind, session_id, occurred_at, before_json, after_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            todo_id,
            action,
            actor.kind,
            actor.session_id,
            occurred_at,
            json.dumps({key: before.get(key) for key in sorted(changed)}, ensure_ascii=False),
            json.dumps({key: after.get(key) for key in sorted(changed)}, ensure_ascii=False),
        ),
    )


def history_item(row: sqlite3.Row, *, details: bool = True) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "action": row["action"],
        "actor": row["actor_kind"],
        "session_id": row["session_id"],
        "occurred_at": row["occurred_at"],
    }
    if details:
        result.update(before=json.loads(row["before_json"]), after=json.loads(row["after_json"]))
    return result


def latest_change(conn: sqlite3.Connection, todo_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM todo_history WHERE todo_id = ? ORDER BY id DESC LIMIT 1", (todo_id,)
    ).fetchone()
    return history_item(row, details=False) if row is not None else None
