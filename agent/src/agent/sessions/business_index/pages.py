"""Version-bound, bounded presentation windows and deferred event bodies."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from ..query import CancelCheck, check_cancel
from ..store_types import SourceVersion
from .index import source_token
from .presentation import dump

_COLUMNS = "sort_position AS position,uuid,kind,summary,bytes,message_id,run_id,result_uuid"


def _state(db: sqlite3.Connection, version: str | None = None) -> sqlite3.Row:
    row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
    if row is None:
        raise ValueError("History projection unavailable; retry the latest page")
    if version is not None and row["epoch"] != version:
        raise ValueError("History content changed; reload the latest page")
    return cast(sqlite3.Row, row)


def _recovery(db: sqlite3.Connection) -> dict[str, str] | None:
    user = db.execute(
        "SELECT position,kind FROM events WHERE active=1 AND kind IN ('user_message','system_trigger') "
        "ORDER BY position DESC LIMIT 1"
    ).fetchone()
    if user is None or user["kind"] != "user_message":
        return None
    latest = db.execute(
        "SELECT run_id,kind FROM events WHERE active=1 AND position>=? "
        "AND kind IN ('run_started','run_terminal','run_error') ORDER BY position DESC LIMIT 1",
        (user["position"],),
    ).fetchone()
    if latest is None or not latest["run_id"] or latest["kind"] == "run_started":
        return None
    row = db.execute("SELECT data FROM runs WHERE id=?", (latest["run_id"],)).fetchone()
    if row is None:
        return None
    run = json.loads(row["data"])
    if run.get("status") == "completed" or not (
        run.get("status") == "interrupted" or run.get("recoverable") is True
    ):
        return None
    if db.execute(
        "SELECT 1 FROM events WHERE active=1 AND kind='tool_call_request' "
        "AND position>=? AND result_uuid IS NULL LIMIT 1",
        (user["position"],),
    ).fetchone():
        return None
    return {
        "sourceRunId": latest["run_id"],
        "rootRunId": run.get("root_run_id") or latest["run_id"],
    }


def _envelope(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    version: str,
    *,
    inline_limit: int,
    recovery: dict[str, str] | None,
    paired: bool = False,
) -> dict[str, Any]:
    if row["bytes"] <= inline_limit:
        raw = db.execute("SELECT body FROM events WHERE uuid=?", (row["uuid"],)).fetchone()[0]
        result: dict[str, Any] = json.loads(raw)
        facts = json.loads(row["summary"])
        if "summary" in facts:
            result["summary"] = facts["summary"]
        # Provider continuation data remains private on every presentation path.
        if result["type"] == "provider_reasoning_state":
            result["payload"] = {"redacted": True}
        result["body"] = {"state": "inline"}
    else:
        result = json.loads(row["summary"])
        result["body"] = {
            "state": "deferred",
            "ref": row["uuid"],
            "byte_length": row["bytes"],
            "content_version": version,
        }
    result["message_id"] = row["message_id"] or row["uuid"]
    if not paired:
        bounds = db.execute(
            "SELECT MIN(sort_position),MAX(sort_position) FROM events WHERE active=1 AND display=1 AND message_id=?",
            (row["message_id"],),
        ).fetchone()
        result["message_start"] = bounds[0] == row["position"]
        result["message_end"] = bounds[1] == row["position"]
    if row["run_id"]:
        data = db.execute("SELECT data FROM runs WHERE id=?", (row["run_id"],)).fetchone()
        run = json.loads(data["data"]) if data else {"run_id": row["run_id"]}
        run["can_continue"] = bool(recovery and recovery["sourceRunId"] == row["run_id"])
        result["run"] = run
    if row["result_uuid"] and not paired:
        other = db.execute(
            f"SELECT {_COLUMNS} FROM events WHERE uuid=? AND active=1",
            (row["result_uuid"],),
        ).fetchone()
        if other:
            result["result_event"] = _envelope(
                db,
                other,
                version,
                inline_limit=inline_limit,
                recovery=recovery,
                paired=True,
            )
    return result


def _position(db: sqlite3.Connection, cursor: str, *, around: bool = False) -> float:
    row = db.execute(
        "SELECT sort_position AS position,kind,display,active FROM events WHERE uuid=?", (cursor,)
    ).fetchone()
    if row is None or not row["active"]:
        raise ValueError("History cursor is no longer available")
    if around and row["kind"] == "tool_call_result":
        parent = db.execute(
            "SELECT sort_position FROM events WHERE result_uuid=? AND active=1", (cursor,)
        ).fetchone()
        if parent:
            return float(parent[0])
    if not row["display"] and not around:
        raise ValueError("History cursor must name a visible event")
    return float(row["position"])


def page(
    db: sqlite3.Connection,
    version: SourceVersion,
    *,
    before: str | None = None,
    after: str | None = None,
    around: str | None = None,
    epoch: str | None = None,
    message_id: str | None = None,
    item_limit: int = 80,
    byte_limit: int = 262144,
    cancel_check: CancelCheck = None,
) -> dict[str, Any]:
    if sum(value is not None for value in (before, after, around)) > 1:
        raise ValueError("Use only one history direction")
    if not 1 <= item_limit <= 200 or not 4096 <= byte_limit <= 1024 * 1024:
        raise ValueError("Invalid history page bounds")
    state = _state(db)
    if source_token(tuple(json.loads(state["version"]))) != source_token(version):
        raise ValueError("History changed; reload the latest page")
    # Bodies are immutable within an epoch. A durable append must not invalidate
    # an older body's in-flight chunks; rewrites/rebuilds establish a new epoch.
    token = state["epoch"]
    if epoch is not None and epoch != state["epoch"]:
        raise ValueError("History changed; reload the latest page")
    filters = "active=1 AND display=1"
    params: list[Any] = []
    if message_id is not None:
        filters += " AND message_id=?"
        params.append(message_id)
    descending = after is None and around is None
    if before is not None:
        filters += " AND sort_position<?"
        params.append(_position(db, before))
    elif after is not None:
        filters += " AND sort_position>?"
        params.append(_position(db, after))
    elif around is not None:
        position = _position(db, around, around=True)
        # The target must survive the byte bound; callers can page both sides.
        filters += " AND sort_position>=?"
        params.append(position)
    elif message_id is not None:
        descending = False
    order = "DESC" if descending else "ASC"
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM events WHERE {filters} ORDER BY sort_position {order} LIMIT ?",
        (*params, item_limit),
    ).fetchall()
    recovery = _recovery(db)
    result: dict[str, Any] = {
        **json.loads(state["metadata"]),
        "history_epoch": state["epoch"],
        "content_version": token,
        "events": [],
        "recovery": recovery,
        "before_cursor": None,
        "after_cursor": None,
        "has_before": False,
        "has_after": False,
    }
    used = len(dump(result).encode("utf-8")) + 512
    chosen: list[tuple[sqlite3.Row, dict[str, Any]]] = []
    for row in rows:
        check_cancel(cancel_check)
        event = _envelope(
            db, row, token, inline_limit=min(8192, byte_limit // 4), recovery=recovery
        )
        size = len(dump(event).encode("utf-8")) + 1
        if used + size > byte_limit:
            event = _envelope(db, row, token, inline_limit=0, recovery=recovery)
            size = len(dump(event).encode("utf-8")) + 1
        if used + size > byte_limit:
            if not chosen:
                raise ValueError("History page bound is too small for required event facts")
            break
        used += size
        chosen.append((row, event))
    if descending:
        chosen.reverse()
    if chosen:
        first, last = chosen[0][0], chosen[-1][0]
        extra = " AND message_id=?" if message_id is not None else ""
        matching = [message_id] if message_id is not None else []
        result.update(
            before_cursor=first["uuid"],
            after_cursor=last["uuid"],
            has_before=bool(
                db.execute(
                    f"SELECT 1 FROM events WHERE active=1 AND display=1 AND sort_position<?{extra} LIMIT 1",
                    [first["position"], *matching],
                ).fetchone()
            ),
            has_after=bool(
                db.execute(
                    f"SELECT 1 FROM events WHERE active=1 AND display=1 AND sort_position>?{extra} LIMIT 1",
                    [last["position"], *matching],
                ).fetchone()
            ),
            events=[event for _, event in chosen],
        )
    return result


def content(
    db: sqlite3.Connection,
    ref: str,
    *,
    version: str,
    offset: int = 0,
    limit: int = 65536,
    cancel_check: CancelCheck = None,
) -> dict[str, Any]:
    _state(db, version)
    if offset < 0 or not 4 <= limit <= 262144:
        raise ValueError("Invalid history content bounds")
    row = db.execute(
        "SELECT kind,length(body) AS total,substr(body,?,?) AS chunk FROM events "
        "WHERE uuid=? AND active=1",
        (offset + 1, limit, ref),
    ).fetchone()
    if row is None:
        raise ValueError("History content is no longer available")
    if row["kind"] == "provider_reasoning_state":
        raise ValueError("Internal continuation data is not public history")
    if offset > row["total"]:
        raise ValueError("History content offset is out of range")
    check_cancel(cancel_check)
    text = row["chunk"].encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    next_offset = offset + len(text)
    return {
        "text": text,
        "offset": offset,
        "next_offset": next_offset,
        "total_chars": row["total"],
        "complete": next_offset == row["total"],
        "content_version": version,
    }
