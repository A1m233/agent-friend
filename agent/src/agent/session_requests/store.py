"""Durable input receipts; execution is exclusively owned by RunScheduler."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..paths import session_requests_db_path
from ..runtime.models import RunSnapshot


class SessionRequestStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or session_requests_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS session_requests (
                run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_event_id TEXT NOT NULL,
                input_json TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT,
                reason_code TEXT NOT NULL DEFAULT '', input_accepted INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT -1, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT)""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS request_session ON session_requests(session_id, created_at)"
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(session_requests)")}
            if "effective_options_json" not in columns:
                conn.execute("ALTER TABLE session_requests ADD COLUMN effective_options_json TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def admit(
        self, run_id: str, session_id: str, user_event_id: str, payload: dict[str, Any]
    ) -> bool:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM session_requests WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is not None:
                if (row["session_id"], row["user_event_id"], row["input_json"]) != (
                    session_id,
                    user_event_id,
                    encoded,
                ):
                    raise ValueError("request_id_conflict")
                return False
            conn.execute(
                "INSERT INTO session_requests(run_id,session_id,user_event_id,input_json,status,created_at) VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    session_id,
                    user_event_id,
                    encoded,
                    "queued",
                    datetime.now(UTC).isoformat(),
                ),
            )
            return True

    def observe(self, snapshot: RunSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE session_requests SET status=?,outcome=?,reason_code=?,version=?,
                input_accepted=MAX(input_accepted,?),started_at=COALESCE(started_at,?),finished_at=?
                WHERE run_id=? AND status!='terminal' AND version<?""",
                (
                    snapshot.status.value,
                    "interrupted"
                    if snapshot.outcome and snapshot.reason_code == "runtime_stopping"
                    else snapshot.outcome.value
                    if snapshot.outcome
                    else None,
                    snapshot.reason_code,
                    snapshot.version,
                    int(bool(snapshot.input_accepted)),
                    snapshot.started_at.isoformat() if snapshot.started_at else None,
                    snapshot.finished_at.isoformat() if snapshot.finished_at else None,
                    snapshot.run_id,
                    snapshot.version,
                ),
            )

    def settle(self, run_id: str, outcome: str, reason: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE session_requests SET status='terminal',outcome=?,reason_code=?,finished_at=? WHERE run_id=? AND status!='terminal'",
                (outcome, reason, datetime.now(UTC).isoformat(), run_id),
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_requests WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def record_effective_options(self, run_id: str, options: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE session_requests SET effective_options_json=? WHERE run_id=? AND status!='terminal'",
                (json.dumps(options, ensure_ascii=False), run_id),
            )

    def list(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_requests WHERE session_id=? ORDER BY created_at DESC,run_id LIMIT ?",
                (session_id, max(1, min(limit, 100))),
            ).fetchall()
        return [self._decode(row) for row in reversed(rows)]

    def recover(self, terminal: Callable[[str, str], tuple[str, str] | None]) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_requests WHERE status!='terminal'"
            ).fetchall()
        for row in rows:
            known = terminal(row["session_id"], row["run_id"])
            outcome, reason = known or ("interrupted", "runtime_restarted")
            self.settle(row["run_id"], outcome, reason)

    def referenced_assets(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT input_json FROM session_requests WHERE status!='terminal'"
            ).fetchall()
        return {
            ref["asset_id"]
            for row in rows
            for ref in json.loads(row["input_json"]).get("options", {}).get("attachments", [])
        }

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["input"] = json.loads(result.pop("input_json"))
        result["input_accepted"] = bool(result["input_accepted"])
        effective = result.pop("effective_options_json")
        result["effective_options"] = json.loads(effective) if effective else None
        return result
