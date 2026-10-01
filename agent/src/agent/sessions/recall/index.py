"""Disposable per-session SQLite projection; the JSONL remains authoritative.

All connections are short-lived and covered by a sidecar lock. This also makes
replacing a corrupt derived database safe without unlinking another reader's DB.
No database/file lock is held during a full source scan.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from types import MappingProxyType

import portalocker
from portalocker.exceptions import LockException

from ..events import ROLLOUT_EVENT_TYPES, Event
from ..query import CancelCheck, check_cancel
from ..store_types import SessionLoadDiagnostics, SourceVersion
from .snapshot import SessionRecallSnapshot, conversation_event, inactive_uuids

_SCHEMA = 1


class InvalidProjection(ValueError):
    """Derived rows cannot be decoded; rebuild without touching the source."""


_DDL = (
    "CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, "
    "count INTEGER NOT NULL, diagnostics TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)",
    "CREATE TABLE positions (uuid TEXT PRIMARY KEY, position INTEGER NOT NULL)",
    "CREATE TABLE inactive (uuid TEXT PRIMARY KEY)",
    "CREATE TABLE utterances (position INTEGER PRIMARY KEY, uuid TEXT NOT NULL, "
    "kind TEXT NOT NULL, ts TEXT NOT NULL, content TEXT NOT NULL, persona TEXT)",
    "CREATE INDEX utterance_uuid ON utterances(uuid)",
)


class RecallIndex:
    def __init__(self, base_dir: Path) -> None:
        self.root = base_dir / ".recall"

    def path(self, session_id: str) -> Path:
        return self.root / (sha256(session_id.encode("utf-8")).hexdigest() + ".sqlite3")

    @contextmanager
    def _locked(
        self,
        path: Path,
        cancel_check: CancelCheck,
        *,
        wait: bool = True,
    ) -> Iterator[None]:
        check_cancel(cancel_check)
        self.root.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            path.with_suffix(".lock"),
            "a+b",
            timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        deadline = monotonic() + (10 if wait else 0)
        while True:
            check_cancel(cancel_check)
            try:
                lock.acquire()
                break
            except LockException:
                if monotonic() >= deadline:
                    raise
                # Lock's bounded polling avoids busy spinning and checks cancellation.
                lock.timeout = min(0.05, max(0, deadline - monotonic()))
                lock.check_interval = 0.01
        try:
            check_cancel(cancel_check)
            yield
        finally:
            lock.release()

    @contextmanager
    def _database(self, path: Path, cancel_check: CancelCheck) -> Iterator[sqlite3.Connection]:
        cancelled: list[BaseException] = []

        def progress() -> int:
            try:
                check_cancel(cancel_check)
            except BaseException as exc:
                cancelled.append(exc)
                return 1
            return 0

        with closing(sqlite3.connect(path, timeout=0)) as db:
            db.row_factory = sqlite3.Row
            db.set_progress_handler(progress, 1000)
            try:
                # Explicit transaction also makes the multi-table read one snapshot.
                db.execute("BEGIN")
                with db:
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    tables = {
                        row[0]
                        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    if (
                        version != _SCHEMA
                        or not {"state", "positions", "inactive", "utterances"} <= tables
                    ):
                        for name in ("state", "positions", "inactive", "utterances"):
                            db.execute(f"DROP TABLE IF EXISTS {name}")
                        for statement in _DDL:
                            db.execute(statement)
                        db.execute(f"PRAGMA user_version={_SCHEMA}")
                    yield db
                    check_cancel(cancel_check)
            except sqlite3.Error:
                if cancelled:
                    raise cancelled[0] from None
                raise

    def _remove_locked(self, path: Path) -> None:
        # Only our derived files, with every connection closed under the sidecar lock.
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)

    def read(
        self,
        session_id: str,
        version: SourceVersion,
        cancel_check: CancelCheck = None,
    ) -> SessionRecallSnapshot | None:
        path = self.path(session_id)
        if not path.exists():
            return None
        with self._locked(path, cancel_check):
            try:
                with self._database(path, cancel_check) as db:
                    row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
                    if row is None or row["version"] != json.dumps(version):
                        return None
                    positions = {}
                    for item in db.execute("SELECT * FROM positions"):
                        check_cancel(cancel_check)
                        positions[item["uuid"]] = item["position"]
                    events = []
                    for item in db.execute("SELECT * FROM utterances ORDER BY position"):
                        check_cancel(cancel_check)
                        try:
                            if item["kind"] not in {"user_message", "assistant_message"}:
                                raise ValueError("invalid recall event kind")
                            events.append(
                                Event(
                                    type=item["kind"],
                                    uuid=item["uuid"],
                                    ts=datetime.fromisoformat(item["ts"]),
                                    payload={"content": item["content"]},
                                    meta={"persona_id": item["persona"]} if item["persona"] else {},
                                )
                            )
                        except (ValueError, TypeError) as exc:
                            raise InvalidProjection from exc
                    try:
                        diagnostics = SessionLoadDiagnostics(**json.loads(row["diagnostics"]))
                        created = datetime.fromisoformat(row["created"])
                        updated = datetime.fromisoformat(row["updated"])
                    except (ValueError, TypeError) as exc:
                        raise InvalidProjection from exc
                    return SessionRecallSnapshot(
                        session_id=session_id,
                        events=tuple(events),
                        raw_event_indices=MappingProxyType(positions),
                        raw_event_count=row["count"],
                        source_version=version,
                        diagnostics=diagnostics,
                        created_at=created,
                        updated_at=updated,
                    )
            except sqlite3.DatabaseError as exc:
                if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) not in {
                    sqlite3.SQLITE_CORRUPT,
                    sqlite3.SQLITE_NOTADB,
                    sqlite3.SQLITE_SCHEMA,
                }:
                    raise
                self._remove_locked(path)
                return None
            except InvalidProjection:
                self._remove_locked(path)
                return None

    def publish(
        self,
        snapshot: SessionRecallSnapshot,
        inactive: set[str],
        is_current: Callable[[], bool],
        cancel_check: CancelCheck = None,
    ) -> bool:
        path = self.path(snapshot.session_id)
        with self._locked(path, cancel_check), self._database(path, cancel_check) as db:
            if not is_current():
                return False
            for table in ("state", "positions", "inactive", "utterances"):
                db.execute(f"DELETE FROM {table}")
            for uuid, position in snapshot.raw_event_indices.items():
                check_cancel(cancel_check)
                db.execute("INSERT INTO positions VALUES (?, ?)", (uuid, position))
            for uuid in inactive:
                check_cancel(cancel_check)
                db.execute("INSERT INTO inactive VALUES (?)", (uuid,))
            for event in snapshot.events:
                check_cancel(cancel_check)
                self._insert_event(db, snapshot.raw_event_indices[event.uuid], event)
            db.execute(
                "INSERT INTO state VALUES (1, ?, ?, ?, ?, ?)",
                (
                    json.dumps(snapshot.source_version),
                    snapshot.raw_event_count,
                    json.dumps(asdict(snapshot.diagnostics)),
                    snapshot.created_at.isoformat(),
                    snapshot.updated_at.isoformat(),
                ),
            )
            return True

    @staticmethod
    def _insert_event(db: sqlite3.Connection, position: int, event: Event) -> None:
        db.execute(
            "INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?)",
            (
                position,
                event.uuid,
                event.type,
                event.ts.isoformat(),
                event.payload["content"],
                event.meta.get("persona_id"),
            ),
        )

    def advance(
        self,
        session_id: str,
        before: SourceVersion,
        after: SourceVersion,
        events: list[Event],
        is_current: Callable[[], bool],
    ) -> None:
        path = self.path(session_id)
        if not path.exists():
            return
        with self._locked(path, None, wait=False), self._database(path, None) as db:
            row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
            if (
                row is None
                or row["version"] != json.dumps(before)
                or not is_current()
                or json.loads(row["diagnostics"])["parse_error_count"]
            ):
                return
            updated = row["updated"]
            for position, event in enumerate(events, start=row["count"]):
                if event.type in ROLLOUT_EVENT_TYPES:
                    continue
                db.execute("INSERT OR REPLACE INTO positions VALUES (?, ?)", (event.uuid, position))
                for uuid in inactive_uuids(event):
                    db.execute("INSERT OR IGNORE INTO inactive VALUES (?)", (uuid,))
                    db.execute("DELETE FROM utterances WHERE uuid=?", (uuid,))
                if event.type in {"user_message", "assistant_message"}:
                    updated = event.ts.isoformat()
                lean = conversation_event(event)
                if (
                    lean is not None
                    and db.execute(
                        "SELECT 1 FROM inactive WHERE uuid=?",
                        (event.uuid,),
                    ).fetchone()
                    is None
                ):
                    self._insert_event(db, position, lean)
            db.execute(
                "UPDATE state SET version=?, count=?, updated=? WHERE id=1",
                (
                    json.dumps(after),
                    row["count"] + len(events),
                    updated,
                ),
            )

    def delete(self, session_id: str) -> None:
        path = self.path(session_id)
        if path.exists():
            with self._locked(path, None):
                self._remove_locked(path)
