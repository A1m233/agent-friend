"""First-attempt notification records, owned by the scheduled-task database."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

Connection = Callable[[], AbstractContextManager[sqlite3.Connection]]


class ScheduledNotificationStore:
    def __init__(self, connect: Connection, transaction: Connection) -> None:
        self._connect = connect
        self._transaction = transaction

    @staticmethod
    def initialize(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scheduled_notifications (
                occurrence_id TEXT PRIMARY KEY REFERENCES scheduled_occurrences(id),
                state TEXT NOT NULL CHECK(state IN ('pending','ready','settled','skipped','failed')),
                text TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_scheduled_notifications_state
                ON scheduled_notifications(state);
            CREATE TABLE IF NOT EXISTS scheduled_notification_deliveries (
                occurrence_id TEXT NOT NULL REFERENCES scheduled_notifications(occurrence_id),
                destination_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','delivered','failed','unknown','skipped')),
                receipt TEXT,
                error_kind TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                PRIMARY KEY(occurrence_id, destination_id)
            );
            """
        )

    @staticmethod
    def register(conn: sqlite3.Connection, occurrence_id: str) -> None:
        conn.execute(
            "INSERT INTO scheduled_notifications(occurrence_id,state) VALUES (?,'pending')",
            (occurrence_id,),
        )

    def pending_ids(self, limit: int = 20) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT n.occurrence_id FROM scheduled_notifications n "
                "JOIN scheduled_occurrences o ON o.id=n.occurrence_id "
                "WHERE n.state IN ('pending','ready') AND o.state='terminal' "
                "ORDER BY o.finished_at,o.created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def prepare(self, occurrence_id: str, text: str, destinations: tuple[str, ...]) -> None:
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_notifications SET state='ready',text=? "
                "WHERE occurrence_id=? AND state='pending'",
                (text, occurrence_id),
            )
            if cursor.rowcount != 1:
                return
            conn.executemany(
                "INSERT INTO scheduled_notification_deliveries "
                "(occurrence_id,destination_id,state) VALUES (?,?,'pending')",
                [(occurrence_id, destination) for destination in sorted(set(destinations))],
            )

    def close_unprepared(self, occurrence_id: str, reason: str, *, failed: bool = False) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE scheduled_notifications SET state=?,reason=? "
                "WHERE occurrence_id=? AND state='pending'",
                ("failed" if failed else "skipped", reason, occurrence_id),
            )

    def text(self, occurrence_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT text FROM scheduled_notifications WHERE occurrence_id=?",
                (occurrence_id,),
            ).fetchone()
        return str(row[0]) if row else ""

    def claim(self, occurrence_id: str, destination_id: str) -> bool:
        # Persist uncertainty before any external effect; never reset it on restart.
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_notification_deliveries SET state='unknown',"
                "error_kind='send_in_progress',attempt_count=attempt_count+1,updated_at=? "
                "WHERE occurrence_id=? AND destination_id=? AND state='pending'",
                (_now(), occurrence_id, destination_id),
            )
        return cursor.rowcount == 1

    def settle(
        self,
        occurrence_id: str,
        destination_id: str,
        *,
        outcome: str,
        receipt: str | None,
        error_kind: str | None,
    ) -> None:
        state = "skipped" if error_kind == "binding_changed" else outcome
        with self._transaction() as conn:
            conn.execute(
                "UPDATE scheduled_notification_deliveries SET state=?,receipt=?,"
                "error_kind=?,updated_at=? WHERE occurrence_id=? AND destination_id=? "
                "AND state='unknown' AND error_kind='send_in_progress'",
                (state, receipt, error_kind, _now(), occurrence_id, destination_id),
            )

    def finish(self, occurrence_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE scheduled_notifications SET state='settled' WHERE occurrence_id=? "
                "AND state='ready' AND NOT EXISTS (SELECT 1 FROM scheduled_notification_deliveries "
                "WHERE occurrence_id=? AND state='pending')",
                (occurrence_id, occurrence_id),
            )

    def summary(self, occurrence_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            plan = conn.execute(
                "SELECT state,reason FROM scheduled_notifications WHERE occurrence_id=?",
                (occurrence_id,),
            ).fetchone()
            rows = conn.execute(
                "SELECT destination_id,state,receipt,error_kind,attempt_count,updated_at "
                "FROM scheduled_notification_deliveries WHERE occurrence_id=? ORDER BY destination_id",
                (occurrence_id,),
            ).fetchall()
        return {
            "state": str(plan["state"]) if plan else "disabled",
            "reason": str(plan["reason"]) if plan else "",
            "deliveries": [dict(row) for row in rows],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()
