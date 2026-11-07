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
from ..message_access import MessagePage, read_message_page
from ..query import CancelCheck, SessionHistoryPage, SessionQuerySnapshot, check_cancel
from ..read_views.display import display_event
from ..read_views.journal import HistoryWindow
from ..session import Session
from ..store_types import SessionLoadDiagnostics, SourceVersion
from . import chat_tail
from .pages import page, recovery_body


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_SCHEMA = 4
_CHAT_BODIES = "CREATE TABLE IF NOT EXISTS chat_bodies (uuid TEXT PRIMARY KEY, body TEXT NOT NULL)"
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
                    db.execute("PRAGMA journal_mode=WAL")
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
                        db.execute(_CHAT_BODIES)
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

    def read_existing(
        self, session_id: str, version: SourceVersion, cancel_check: CancelCheck
    ) -> Session | None:
        """Return an owned business session without migrating or building a database."""
        path = self.path(session_id)
        if not path.exists():
            return None
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA:
                return None
            state = db.execute(
                "SELECT diagnostics FROM state WHERE version=?", (dump(version),)
            ).fetchone()
            if state is None:
                return None
            events = []
            for (body,) in db.execute("SELECT body FROM events ORDER BY position"):
                check_cancel(cancel_check)
                events.append(Event.from_jsonl(body))
            session = Session.from_events(events)
            session.load_diagnostics = SessionLoadDiagnostics(**json.loads(state[0]))
            session.loaded_source_version = version
            return session

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
            if db.execute("SELECT 1 FROM sqlite_schema WHERE name='chat_tail_state'").fetchone():
                db.execute("DELETE FROM chat_tail_state")
                db.execute("DELETE FROM chat_tail_rows")
            for table in ("state", "events", "inactive", "chat_bodies"):
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
        db.execute(
            "INSERT OR REPLACE INTO chat_bodies VALUES (?,?)",
            (event.uuid, display_event(event).to_jsonl()),
        )

    def advance(
        self,
        session_id: str,
        before: SourceVersion,
        after: SourceVersion,
        events: list[Event],
        is_current: Callable[[], bool],
        encoded: list[bytes] | None = None,
    ) -> None:
        if not self.path(session_id).exists():
            return
        with self.database(session_id, wait=False) as db:
            if not is_current():
                return
            if encoded is not None:

                def positioned() -> Iterator[tuple[int, Event]]:
                    offset = before[2]
                    for event, data in zip(events, encoded, strict=True):
                        yield offset, event
                        offset += len(data)

                chat_tail.advance(db, before, after, positioned())
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
                    metadata = json.loads(row["metadata"])
                    session = Session.new(
                        metadata["title"],
                        metadata["persona"],
                        metadata["model"],
                        session_id=session_id,
                        reasoning_preset=metadata["reasoningPreset"],
                    )
                    session.events.extend(events)
                    metadata.update(
                        persona=session.current_persona,
                        model=session.current_model,
                        reasoningPreset=session.current_model_config.reasoning_preset,
                    )
                    db.execute("UPDATE state SET metadata=? WHERE id=1", (dump(metadata),))
        if current is not None:
            self.publish(current, is_current)

    def page(self, session_id: str, version: SourceVersion, **kwargs: Any) -> SessionHistoryPage:
        with self.database(session_id, kwargs.get("cancel_check")) as db:
            return page(db, version, **kwargs)

    def page_nowait(
        self, session_id: str, version: SourceVersion, **kwargs: Any
    ) -> SessionHistoryPage | None:
        """Read a compatible snapshot only; never create, migrate or take a file lock."""
        path = self.path(session_id)
        if not path.exists():
            return None
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA:
                return None
            state = db.execute("SELECT version FROM state WHERE id=1").fetchone()
            if state is None or state["version"] != dump(version):
                return None
            missing: dict[str, str] = {}
            result = page(db, version, display=True, missing=missing, **kwargs)
        if missing:
            # Older compatible business index: fill only the rows just requested.
            try:
                with self.database(session_id, kwargs.get("cancel_check"), wait=False) as db:
                    if db.execute(
                        "SELECT 1 FROM state WHERE version=?", (dump(version),)
                    ).fetchone():
                        db.executemany(
                            "INSERT OR REPLACE INTO chat_bodies VALUES (?,?)", missing.items()
                        )
            except (sqlite3.Error, OSError, LockException):
                pass
        return result

    def message_page(self, session_id: str, version: SourceVersion, **kwargs: Any) -> MessagePage:
        with self.database(session_id, kwargs.get("cancel_check")) as db:
            return read_message_page(db, version, **kwargs)

    def tail_nowait(
        self, session_id: str, version: SourceVersion, **kwargs: Any
    ) -> HistoryWindow | None:
        path = self.path(session_id)
        if not path.exists():
            return None
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            return chat_tail.read(db, session_id, version, **kwargs)

    def remember_tail(
        self,
        window: HistoryWindow,
        before: str | None,
        is_current: Callable[[], bool],
        check: CancelCheck,
    ) -> None:
        # The old database may have an unknown business schema. Add only the
        # independent display tables, without migrating or replacing that schema.
        path = self.path(window.session_id)
        with (
            self._locked(path.with_suffix(".lock"), check, wait=False),
            closing(sqlite3.connect(path, timeout=0)) as db,
            db,
        ):
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            if is_current():
                chat_tail.cache(db, window, before, check)
                if not is_current():
                    db.rollback()
