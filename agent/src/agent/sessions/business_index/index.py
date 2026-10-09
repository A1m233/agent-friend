"""Per-session, disposable SQLite business history with bounded page reads."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any

import portalocker
from portalocker.exceptions import LockException

from ..events import ROLLOUT_EVENT_TYPES, Event
from ..query import CancelCheck, SessionHistoryPage, SessionQuerySnapshot, check_cancel
from ..store_types import SessionLoadDiagnostics, SourceVersion
from .pages import page, recovery_body


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_SCHEMA = 4
_DDL = (
    "CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, "
    "count INTEGER NOT NULL, diagnostics TEXT NOT NULL, epoch TEXT NOT NULL, metadata TEXT NOT NULL)",
    "CREATE TABLE events (position INTEGER PRIMARY KEY, uuid TEXT UNIQUE NOT NULL, "
    "kind TEXT NOT NULL, body TEXT NOT NULL, active INTEGER NOT NULL, recovery TEXT)",
    "CREATE INDEX active_order ON events(active,position)",
    "CREATE INDEX turn_order ON events(active,kind,position)",
    "CREATE TABLE inactive (uuid TEXT PRIMARY KEY)",
)


class BusinessIndex:
    def __init__(self, base_dir: Path) -> None:
        self.root = base_dir / ".business"

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
        self.root.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            path,
            "a+b",
            timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        deadline = monotonic() + (60 if wait else 0)
        while True:
            check_cancel(cancel_check)
            try:
                lock.acquire()
                break
            except LockException:
                if monotonic() >= deadline:
                    raise
                lock.timeout = min(0.05, max(0, deadline - monotonic()))
                lock.check_interval = 0.01
        try:
            check_cancel(cancel_check)
            yield
        finally:
            lock.release()

    def building(self, session_id: str, cancel_check: CancelCheck = None) -> Any:
        return self._locked(self.path(session_id).with_suffix(".building.lock"), cancel_check)

    @contextmanager
    def database(
        self,
        session_id: str,
        cancel_check: CancelCheck = None,
        *,
        wait: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        path = self.path(session_id)
        cancelled: list[BaseException] = []

        def progress() -> int:
            try:
                check_cancel(cancel_check)
            except BaseException as exc:
                cancelled.append(exc)
                return 1
            return 0

        with self._locked(path.with_suffix(".lock"), cancel_check, wait=wait):
            try:
                with closing(sqlite3.connect(path, timeout=0)) as db:
                    db.row_factory = sqlite3.Row
                    db.set_progress_handler(progress, 1000)
                    db.execute("BEGIN")
                    with db:
                        version = db.execute("PRAGMA user_version").fetchone()[0]
                        tables = {
                            row[0]
                            for row in db.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        if version != _SCHEMA or not {"state", "events", "inactive"} <= tables:
                            for table in ("state", "events", "inactive", "runs"):
                                db.execute(f"DROP TABLE IF EXISTS {table}")
                            for statement in _DDL:
                                db.execute(statement)
                            db.execute(f"PRAGMA user_version={_SCHEMA}")
                        yield db
                        check_cancel(cancel_check)
            except sqlite3.Error as exc:
                if cancelled:
                    raise cancelled[0] from None
                if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in {
                    sqlite3.SQLITE_CORRUPT,
                    sqlite3.SQLITE_NOTADB,
                    sqlite3.SQLITE_SCHEMA,
                }:
                    self._remove(path)
                raise

    @staticmethod
    def _remove(path: Path) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)

    def delete(self, session_id: str) -> None:
        path = self.path(session_id)
        if path.exists():
            with self._locked(path.with_suffix(".lock"), None):
                self._remove(path)

    def matches(
        self,
        session_id: str,
        version: SourceVersion,
        cancel_check: CancelCheck = None,
    ) -> bool:
        if not self.path(session_id).exists():
            return False
        with self.database(session_id, cancel_check) as db:
            row = db.execute("SELECT version FROM state WHERE id=1").fetchone()
            return row is not None and row["version"] == dump(version)

    @staticmethod
    def _snapshot(
        db: sqlite3.Connection, row: sqlite3.Row, cancel_check: CancelCheck
    ) -> SessionQuerySnapshot:
        def records() -> Iterator[tuple[int, Event]]:
            for item in db.execute("SELECT position,body FROM events ORDER BY position"):
                check_cancel(cancel_check)
                yield item["position"], Event.from_jsonl(item["body"])

        snapshot = SessionQuerySnapshot.build(
            records(),
            raw_event_count=row["count"],
            diagnostics=SessionLoadDiagnostics(**json.loads(row["diagnostics"])),
            source_version=tuple(json.loads(row["version"])),
            cancel_check=cancel_check,
        )
        return replace(snapshot, projection_epoch=row["epoch"])

    def read(
        self,
        session_id: str,
        version: SourceVersion,
        cancel_check: CancelCheck = None,
    ) -> SessionQuerySnapshot | None:
        if not self.path(session_id).exists():
            return None
        with self.database(session_id, cancel_check) as db:
            row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
            if row is None or row["version"] != dump(version):
                return None
            return self._snapshot(db, row, cancel_check)

    @staticmethod
    def _metadata(snapshot: SessionQuerySnapshot) -> dict[str, Any]:
        return {
            "session_id": snapshot.session_id,
            "title": snapshot.initial_title,
            "persona": snapshot.current_persona,
            "model": snapshot.current_model,
            "reasoningPreset": snapshot.current_model_config.reasoning_preset,
            "history_epoch": snapshot.history_epoch,
        }

    def publish(
        self,
        snapshot: SessionQuerySnapshot,
        is_current: Callable[[], bool],
        cancel_check: CancelCheck = None,
    ) -> bool:
        if snapshot.source_version is None:
            return False
        with self.database(snapshot.session_id, cancel_check) as db:
            if not is_current():
                return False
            for table in ("state", "events", "inactive"):
                db.execute(f"DELETE FROM {table}")
            inactive = {
                str(uuid)
                for event in snapshot.events
                if event.type == "turn_rewrite"
                for uuid in event.payload.get("inactive_event_uuids", [])
                if isinstance(uuid, str)
            }
            db.executemany("INSERT INTO inactive VALUES (?)", [(uuid,) for uuid in inactive])
            for event in snapshot.events:
                check_cancel(cancel_check)
                self._insert(db, snapshot.raw_event_indices[event.uuid], event)
            db.execute(
                "INSERT INTO state VALUES (1,?,?,?,?,?)",
                (
                    dump(snapshot.source_version),
                    snapshot.raw_event_count,
                    dump(asdict(snapshot.load_diagnostics)),
                    snapshot.projection_epoch,
                    dump(self._metadata(snapshot)),
                ),
            )
            if not is_current():
                db.rollback()
                return False
            return True

    @staticmethod
    def _insert(db: sqlite3.Connection, position: int, event: Event) -> None:
        if event.type in ROLLOUT_EVENT_TYPES:
            return
        active = event.type != "turn_rewrite" and not bool(
            db.execute("SELECT 1 FROM inactive WHERE uuid=?", (event.uuid,)).fetchone()
        )
        db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (position, event.uuid, event.type, event.to_jsonl(), int(active), recovery_body(event)),
        )

    def advance(
        self,
        session_id: str,
        before: SourceVersion,
        after: SourceVersion,
        events: list[Event],
        is_current: Callable[[], bool],
    ) -> None:
        if not self.path(session_id).exists():
            return
        with self.database(session_id, wait=False) as db:
            row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
            if row is None or row["version"] != dump(before) or not is_current():
                return
            if json.loads(row["diagnostics"])["parse_error_count"]:
                return
            if any(event.type == "turn_rewrite" for event in events):
                # Existing business rows are sufficient; never replay historical rollout.
                current = self._snapshot(db, row, None).appended(events, after)
                # Rebuild after releasing the short database transaction below.
            else:
                current = None
                for position, event in enumerate(events, start=row["count"]):
                    self._insert(db, position, event)
                db.execute(
                    "UPDATE state SET version=?,count=? WHERE id=1",
                    (
                        dump(after),
                        row["count"] + len(events),
                    ),
                )
                if any(
                    event.type
                    in {"persona_change", "model_change", "model_config_change", "channel_change"}
                    for event in events
                ):
                    updated = db.execute("SELECT * FROM state WHERE id=1").fetchone()
                    snapshot = self._snapshot(db, updated, None)
                    db.execute(
                        "UPDATE state SET metadata=? WHERE id=1", (dump(self._metadata(snapshot)),)
                    )
        if current is not None:
            self.publish(current, is_current)

    def page(self, session_id: str, version: SourceVersion, **kwargs: Any) -> SessionHistoryPage:
        with self.database(session_id, kwargs.get("cancel_check")) as db:
            return page(db, version, **kwargs)
