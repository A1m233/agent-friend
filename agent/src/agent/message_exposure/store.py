from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


def content_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MessageExposureStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS exposure (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, message_id TEXT NOT NULL,
                content_version TEXT NOT NULL, extent TEXT NOT NULL,
                observed_at TEXT NOT NULL, UNIQUE(session_id,message_id))""")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(
        self,
        session_id: str,
        message_id: str,
        version: str,
        extent: Literal["partial", "full"],
        *,
        observed_at: datetime,
    ) -> bool:
        if extent not in {"partial", "full"} or observed_at.tzinfo is None:
            raise ValueError("invalid display evidence")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM exposure WHERE session_id=? AND message_id=?",
                (session_id, message_id),
            ).fetchone()
            if (
                previous
                and previous["content_version"] == version
                and previous["extent"] in {"full", extent}
            ):
                return False
            db.execute(
                "INSERT OR REPLACE INTO exposure(session_id,message_id,content_version,extent,observed_at) VALUES (?,?,?,?,?)",
                (session_id, message_id, version, extent, observed_at.isoformat()),
            )
        return True

    def get(self, session_id: str, message_id: str, version: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT extent,observed_at FROM exposure WHERE session_id=? AND message_id=? AND content_version=?",
                (session_id, message_id, version),
            ).fetchone()
        return {
            "scope": "text",
            "extent": row["extent"] if row else "unknown",
            "observed_at": row["observed_at"] if row else None,
            "read_status": "unknown",
        }

    def changes(self, after: int, *, limit: int = 100) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM exposure WHERE sequence>? ORDER BY sequence LIMIT ?",
                (after, limit + 1),
            ).fetchall()
        selected = rows[:limit]
        return {
            "cursor": selected[-1]["sequence"] if selected else after,
            "items": [dict(row) for row in selected],
            "has_more": len(rows) > limit,
        }

    def delete_session(self, session_id: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM exposure WHERE session_id=?", (session_id,))
