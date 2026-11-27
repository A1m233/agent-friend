"""Streaming bootstrap and incremental rewrite of the disposable business view."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict
from hashlib import sha256

from ..events import Event
from ..query import CancelCheck, check_cancel
from ..session import Session
from ..store_types import SessionReadState, SourceVersion

CONFIG_KINDS = (
    "session_meta",
    "persona_change",
    "model_change",
    "model_config_change",
    "channel_change",
)


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def apply_rewrite(db: sqlite3.Connection, event: Event) -> None:
    if event.type != "turn_rewrite":
        return
    ids = [(uid,) for uid in event.payload.get("inactive_event_uuids", []) if isinstance(uid, str)]
    db.executemany("INSERT OR IGNORE INTO inactive VALUES (?)", ids)
    db.executemany("UPDATE events SET active=0 WHERE uuid=?", ids)


def metadata(db: sqlite3.Connection, epoch: str) -> dict[str, object]:
    # Configuration is a separate projection: never hydrate message/tool bodies.
    placeholders = ",".join("?" for _ in CONFIG_KINDS)
    session = Session.from_events(
        [
            Event.from_jsonl(row[0])
            for row in db.execute(
                f"SELECT body FROM events WHERE active=1 AND kind IN ({placeholders}) ORDER BY position",
                CONFIG_KINDS,
            )
        ]
    )
    rewrite = db.execute(
        "SELECT uuid FROM events WHERE kind='turn_rewrite' ORDER BY position DESC LIMIT 1"
    ).fetchone()
    return {
        "session_id": session.session_id,
        "title": session.initial_title,
        "persona": session.current_persona,
        "model": session.current_model,
        "reasoningPreset": session.current_model_config.reasoning_preset,
        "history_epoch": f"{epoch}:{rewrite[0] if rewrite else 'initial'}",
    }


def rebuild(
    db: sqlite3.Connection,
    events: Iterable[Event],
    state: SessionReadState,
    version: SourceVersion,
    insert: Callable[[sqlite3.Connection, int, Event], None],
    is_current: Callable[[], bool],
    cancel_check: CancelCheck,
) -> bool:
    """Consume a validated journal once, retaining only one record in Python."""
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='chat_tail_state'").fetchone():
        db.execute("DELETE FROM chat_tail_state")
        db.execute("DELETE FROM chat_tail_rows")
    for table in (
        "state",
        "events",
        "inactive",
        "chat_bodies",
        "attachment_refs",
        "asset_refs",
        "text_views",
        "journal_positions",
    ):
        db.execute(f"DELETE FROM {table}")
    for position, event in enumerate(events):
        check_cancel(cancel_check)
        apply_rewrite(db, event)
        insert(db, position, event)
    if state.source_version is None or not is_current():
        db.rollback()
        return False
    version = state.source_version
    epoch = sha256(repr(version).encode("utf-8")).hexdigest()[:24]
    db.execute(
        "INSERT INTO state VALUES (1,?,?,?,?,?)",
        (
            dump(version),
            state.event_count,
            dump(asdict(state.diagnostics)),
            epoch,
            dump(metadata(db, epoch)),
        ),
    )
    if not is_current():
        db.rollback()
        return False
    return True
