"""A contiguous suffix of display history for journals without a complete index."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from ...errors import AgentError
from ..continuation import continuation_target
from ..events import ROLLOUT_EVENT_TYPES, Event
from ..query import CancelCheck, check_cancel
from ..read_views.display import display_event
from ..read_views.journal import HistoryWindow, epoch
from ..store_types import SourceVersion
from .pages import recovery_body

_TURN = "kind IN ('user_message','system_trigger')"
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS chat_tail_state (id INTEGER PRIMARY KEY, epoch TEXT, floor INTEGER, read_only INTEGER, generation TEXT)",
    "CREATE TABLE IF NOT EXISTS chat_tail_rows (pos INTEGER PRIMARY KEY, generation TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, recovery TEXT)",
    "CREATE INDEX IF NOT EXISTS chat_tail_order ON chat_tail_rows(generation,pos)",
    f"CREATE INDEX IF NOT EXISTS chat_tail_turns ON chat_tail_rows(generation,pos) WHERE {_TURN}",
)


def _insert(db: sqlite3.Connection, generation: str, pos: int, event: Event) -> None:
    if event.type not in ROLLOUT_EVENT_TYPES:
        db.execute(
            "INSERT OR REPLACE INTO chat_tail_rows VALUES (?,?,?,?,?)",
            (pos, generation, event.type, display_event(event).to_jsonl(), recovery_body(event)),
        )


def cache(
    db: sqlite3.Connection, window: HistoryWindow, before: str | None, check: CancelCheck
) -> None:
    for sql in _SCHEMA:
        db.execute(sql)
    state = db.execute("SELECT * FROM chat_tail_state WHERE id=1").fetchone()
    same = state is not None and state["epoch"] == window.history_epoch
    floor = int(window.history_cursor.split(":")[1]) if window.history_cursor else 0
    if before and (not same or before != f"{window.history_epoch}:{state['floor']}"):
        return  # Never publish a disconnected range as a complete suffix.
    if same and floor >= state["floor"]:
        return
    generation = state["generation"] if same else window.history_epoch
    # Replacing a stale suffix must not synchronously erase every previously
    # visited page. Range probes on the generation index reclaim bounded batches.
    for operator in ("<", ">"):
        db.execute(
            f"DELETE FROM chat_tail_rows WHERE pos IN (SELECT pos FROM chat_tail_rows "
            f"WHERE generation {operator} ? LIMIT 64)",
            (generation,),
        )
    for pos, event in zip(window.positions, window.active_events, strict=True):
        check_cancel(check)
        _insert(db, generation, pos, event)
    db.execute(
        "INSERT OR REPLACE INTO chat_tail_state VALUES (1,?,?,?,?)",
        (window.history_epoch, floor, int(window.read_only), generation),
    )


def advance(
    db: sqlite3.Connection,
    before: SourceVersion,
    after: SourceVersion,
    events: Iterable[tuple[int, Event]],
) -> None:
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='chat_tail_state'").fetchone():
        return
    state = db.execute(
        "SELECT generation FROM chat_tail_state WHERE epoch=?", (epoch(before),)
    ).fetchone()
    if state is None:
        return
    for pos, event in events:
        if event.type == "turn_rewrite":
            # A rewrite can reactivate a different boundary. Rediscover the suffix on demand.
            db.execute("DELETE FROM chat_tail_state")
            return
        _insert(db, state[0], pos, event)
    db.execute("UPDATE chat_tail_state SET epoch=?", (epoch(after),))


def read(
    db: sqlite3.Connection,
    session_id: str,
    version: SourceVersion,
    *,
    turn_limit: int,
    before: str | None,
    history_epoch: str | None,
    cancel_check: CancelCheck,
) -> HistoryWindow | None:
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='chat_tail_state'").fetchone():
        return None
    current = epoch(version)
    state = db.execute("SELECT * FROM chat_tail_state WHERE epoch=?", (current,)).fetchone()
    if state is None:
        return None
    generation = state["generation"]
    if turn_limit < 1:
        raise ValueError("turn_limit must be positive")
    if history_epoch is not None and history_epoch != current:
        raise ValueError("History changed; reload the latest page")
    end = version[2]
    if before:
        prefix, _, pos = before.partition(":")
        if prefix != current or not pos.isdigit() or not 0 < int(pos) < end:
            raise ValueError("history cursor is no longer available")
        end = int(pos)
        if end <= state["floor"]:
            return None
        if not db.execute(
            f"SELECT 1 FROM chat_tail_rows WHERE pos=? AND generation=? AND {_TURN}",
            (end, generation),
        ).fetchone():
            raise ValueError("history cursor must point to a complete turn boundary")
    starts = db.execute(
        f"SELECT pos FROM chat_tail_rows WHERE generation=? AND {_TURN} AND pos<? ORDER BY pos DESC LIMIT ?",
        (generation, end, turn_limit + 1),
    ).fetchall()
    if state["floor"] and len(starts) < turn_limit:
        return None
    more = len(starts) > turn_limit or bool(state["floor"])
    start = starts[turn_limit - 1][0] if more else 0
    events = []
    for row in db.execute(
        "SELECT body FROM chat_tail_rows WHERE generation=? AND pos>=? AND pos<? ORDER BY pos",
        (generation, start, end),
    ):
        check_cancel(cancel_check)
        events.append(Event.from_jsonl(row[0]))
    facts = []
    for row in db.execute(
        "SELECT recovery FROM chat_tail_rows WHERE generation=? AND recovery IS NOT NULL AND "
        f"pos>=(SELECT MAX(pos) FROM chat_tail_rows WHERE generation=? AND {_TURN}) ORDER BY pos",
        (generation, generation),
    ):
        check_cancel(cancel_check)
        facts.append(Event.from_jsonl(row[0]))
    try:
        recovery = continuation_target(facts)
    except AgentError:
        recovery = None
    return HistoryWindow(
        session_id,
        tuple(events),
        f"{current}:{start}" if more else None,
        current,
        recovery,
        bool(state["read_only"]),
    )
