"""Small durable state machine; conversation content stays in SessionStore."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def initial_state() -> dict[str, Any]:
    return {
        "version": 0,
        "enabled": True,
        "inner_session_id": None,
        "activity_paused": False,
        "activity_until": None,
        "contact_paused": False,
        "contact_until": None,
        "next_wake_at": None,
        "active_run_id": None,
        "last_overview_at": None,
        "baselines": {},
        "last_outcome": None,
    }


class CompanionshipStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wakes (
                    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    status TEXT NOT NULL, snapshot TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                    model_calls INTEGER NOT NULL DEFAULT 0, usage TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS daily_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS read_messages (run_id TEXT, session_id TEXT, message_id TEXT,
                    PRIMARY KEY(run_id, session_id, message_id));
            """)
            # Migrate the untouched preview default, preserving explicit user choices.
            db.execute("BEGIN IMMEDIATE")
            if any(col["name"] == "deadline" for col in db.execute("PRAGMA table_info(wakes)")):
                db.execute("ALTER TABLE wakes DROP COLUMN deadline")
                row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
                if row and json.loads(row[0]) == (initial_state() | {"enabled": False}):
                    self._save(db, initial_state())
            db.execute("INSERT OR IGNORE INTO state VALUES (1,?)", (json.dumps(initial_state()),))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self) -> dict[str, Any]:
        with self._connect() as db:
            return dict(json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0]))

    @staticmethod
    def _save(db: sqlite3.Connection, state: dict[str, Any]) -> None:
        state["version"] += 1
        db.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state),))

    def update(self, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = dict(json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0]))
            change(state)
            self._save(db, state)
            return state

    def ensure_inner(self, create: Callable[[], str]) -> str:
        def assign(state: dict[str, Any]) -> None:
            if not state["inner_session_id"]:
                state["inner_session_id"] = create()

        return str(self.update(assign)["inner_session_id"])

    @staticmethod
    def paused(state: dict[str, Any], kind: str, now: datetime) -> bool:
        until = state.get(f"{kind}_until")
        return bool(
            state[f"{kind}_paused"] and (until is None or datetime.fromisoformat(until) > now)
        )

    def control(self, action: str, *, now: datetime, until: str | None = None) -> dict[str, Any]:
        if until is not None:
            parsed = datetime.fromisoformat(until)
            if parsed.tzinfo is None or parsed <= now:
                raise ValueError("until 必须是未来的带时区时间")
            until = parsed.astimezone(UTC).isoformat()

        def change(state: dict[str, Any]) -> None:
            if action in {"enable", "disable"}:
                state["enabled"] = action == "enable"
                if action == "enable" and not state["next_wake_at"]:
                    state["next_wake_at"] = now.isoformat()
            elif action in {"pause_activity", "pause_contact", "resume_activity", "resume_contact"}:
                verb, kind = action.split("_")
                state[f"{kind}_paused"] = verb == "pause"
                state[f"{kind}_until"] = until if verb == "pause" else None
                if action == "resume_activity":
                    state["next_wake_at"] = now.isoformat()
            else:
                raise ValueError("unknown companionship control")

        return self.update(change)

    def begin(self, run_id: str, snapshot: dict[str, Any], *, now: datetime) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            if (
                not state["enabled"]
                or state["active_run_id"]
                or self.paused(state, "activity", now)
            ):
                return False
            if state["next_wake_at"] and datetime.fromisoformat(state["next_wake_at"]) > now:
                return False
            db.execute(
                "INSERT INTO wakes(run_id,started_at,status,snapshot) VALUES(?,?,?,?)",
                (
                    run_id,
                    now.isoformat(),
                    "prepared",
                    json.dumps(snapshot),
                ),
            )
            state["active_run_id"] = run_id
            state["next_wake_at"] = None
            self._save(db, state)
            return True

    def acknowledge(self, run_id: str, *, now: datetime) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            wake = db.execute(
                "SELECT snapshot,acknowledged FROM wakes WHERE run_id=?", (run_id,)
            ).fetchone()
            if wake is None or wake["acknowledged"]:
                return
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            if state["active_run_id"] != run_id:
                raise ValueError("wake no longer active")
            state["baselines"] = json.loads(wake["snapshot"])["baselines"]
            state["evidence_cursor"] = (
                json.loads(wake["snapshot"]).get("evidence_changes", {}).get("cursor", 0)
            )
            state["last_overview_at"] = now.isoformat()
            db.execute("UPDATE wakes SET acknowledged=1,status='running' WHERE run_id=?", (run_id,))
            self._save(db, state)

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT snapshot FROM wakes WHERE run_id=?", (run_id,)).fetchone()
            return dict(json.loads(row[0])) if row else None

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT run_id,started_at,status,model_calls,usage FROM wakes WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return dict(row) if row else None

    def daily_calls(self, day: str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT calls FROM daily_usage WHERE day=?", (day,)).fetchone()
            return int(row[0]) if row else 0

    def record_usage(self, run_id: str, usage: object) -> None:
        if usage is None:
            return
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT usage FROM wakes WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return
            total = json.loads(row[0])
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                total[key] = total.get(key, 0) + int(getattr(usage, key, 0))
            db.execute("UPDATE wakes SET usage=? WHERE run_id=?", (json.dumps(total), run_id))

    def record_read(self, run_id: str, session_id: str, message_ids: list[str]) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO read_messages VALUES (?,?,?)",
                [(run_id, session_id, mid) for mid in message_ids],
            )

    def record_call(self, run_id: str, *, now: datetime) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            if (
                not state["enabled"]
                or state["active_run_id"] != run_id
                or self.paused(state, "activity", now)
            ):
                raise InterruptedError("自主活动已暂停或结束")
            db.execute("UPDATE wakes SET model_calls=model_calls+1 WHERE run_id=?", (run_id,))
            db.execute(
                "INSERT INTO daily_usage VALUES (?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1",
                (now.date().isoformat(),),
            )

    def schedule(
        self,
        run_id: str,
        *,
        now: datetime,
        wake_at: datetime,
        minimum_seconds: int,
        maximum_seconds: int,
    ) -> dict[str, Any]:
        if wake_at.tzinfo is None:
            raise ValueError("wake_at 必须带时区")
        actual = min(
            max(wake_at, now + timedelta(seconds=minimum_seconds)),
            now + timedelta(seconds=maximum_seconds),
        )

        def change(state: dict[str, Any]) -> None:
            if state["active_run_id"] != run_id:
                raise ValueError("仅当前内心活动可安排下次唤醒")
            state["next_wake_at"] = actual.astimezone(UTC).isoformat()

        return self.update(change)

    def finish(self, run_id: str, *, outcome: str, now: datetime, fallback_seconds: int) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            db.execute("UPDATE wakes SET status=? WHERE run_id=?", (outcome, run_id))
            if state["active_run_id"] == run_id:
                state["active_run_id"] = None
                state["last_outcome"] = outcome
                if not state["next_wake_at"] or datetime.fromisoformat(state["next_wake_at"]) < now:
                    state["next_wake_at"] = (now + timedelta(seconds=fallback_seconds)).isoformat()
                self._save(db, state)

    def forget_session(self, session_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            if state["inner_session_id"] == session_id:
                state = initial_state() | {"version": state["version"], "enabled": False}
                db.execute("DELETE FROM wakes")
                db.execute("DELETE FROM read_messages")
            else:
                state["baselines"].pop(session_id, None)
                db.execute("DELETE FROM read_messages WHERE session_id=?", (session_id,))
                # Snapshots retain only IDs/counters; purge even those for deleted conversations.
                for row in db.execute("SELECT run_id,snapshot FROM wakes").fetchall():
                    snapshot = json.loads(row["snapshot"])
                    snapshot["sessions"].pop(session_id, None)
                    snapshot["baselines"].pop(session_id, None)
                    snapshot["entries"] = [
                        entry for entry in snapshot["entries"] if entry["session_id"] != session_id
                    ]
                    db.execute(
                        "UPDATE wakes SET snapshot=? WHERE run_id=?",
                        (json.dumps(snapshot), row["run_id"]),
                    )
            self._save(db, state)
