"""Unified lightweight, point and range reads over a versioned business index."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from ..business_index.cancellation import sticky_cancellation
from ..errors import SessionCorruptError, SessionPersistError
from ..events import Event
from ..message_access import MessagePage
from ..query import CancelCheck, check_cancel
from ..recall.snapshot import SessionRecallSnapshot
from ..store_types import SourceVersion
from .observation import ReadCost, observe_read
from .snapshot import SessionRead
from .types import EventFilter, SessionState

if TYPE_CHECKING:
    from ..store import JsonlSessionStore, SessionStore, SessionSummary


class SessionReader:
    def __init__(self, store: JsonlSessionStore) -> None:
        self._store = store

    def exists(self, session_id: str) -> bool:
        return self._store.exists(session_id)

    def version(self, session_id: str) -> SourceVersion:
        from ..errors import SessionNotFoundError

        path = self._store._existing_path(session_id)
        if path is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        return self._store._source_version(path)

    def state(self, session_id: str, *, cancel_check: CancelCheck = None) -> SessionState:
        with observe_read(session_id, "state") as cost:
            version = self.version(session_id)
            session = self._store.composer_session(session_id, cancel_check=cancel_check)
            if session.session_id != session_id:
                raise SessionCorruptError("session identity mismatch")
            result = SessionState.from_session(session, version)
            head = self._store.session_head(session_id)
            result = replace(
                result, initial_persona=head.initial_persona, initial_model=head.initial_model
            )
            if result.session_id != session_id:
                raise SessionCorruptError("session identity mismatch")
            if self.version(session_id) != version:
                raise ValueError("history_changed")
            cost.version = version
            cost.rows = 1
            return result

    def summary(self, session_id: str) -> SessionSummary:
        from ..errors import SessionCorruptError

        version = self.version(session_id)
        path = self._store._existing_path(session_id)
        assert path is not None
        summary = self._store._read_summary(path)
        if summary is None:
            raise SessionCorruptError(f"会话摘要不可读取: {session_id}")
        if self.version(session_id) != version:
            raise ValueError("history_changed")
        return summary

    def assistant_turn(self, session_id: str, user_event_id: str) -> tuple[Event, ...]:
        with self.snapshot(session_id) as read:
            rows = read.events(EventFilter(ids=(user_event_id,), kinds=("user_message",)), limit=1)
            if not rows:
                return ()
            position, user = rows[0]
            boundary = read.events(
                EventFilter(kinds=("user_message", "system_trigger"), after=position), limit=1
            )
            end = boundary[0][0] if boundary else read.event_count
            run_id = user.meta.get("run_id")
            return tuple(
                event
                for _, event in read.scan(
                    EventFilter(kinds=("assistant_message",), after=position, before=end),
                    purpose="outbound answer sources",
                )
                if event.payload.get("partial") is not True
                and (run_id is None or event.meta.get("run_id") == run_id)
            )

    def identity(self, session_id: str) -> SessionState:
        with observe_read(session_id, "identity") as cost:
            version = self.version(session_id)
            result = SessionState.from_session(self._store.session_head(session_id), version)
            if result.session_id != session_id:
                raise SessionCorruptError("session identity mismatch")
            if self.version(session_id) != version:
                raise ValueError("history_changed")
            cost.version = version
            cost.rows = 1
            return result

    @contextmanager
    def snapshot(
        self, session_id: str, *, cancel_check: CancelCheck = None
    ) -> Iterator[SessionRead]:
        cancel_check = sticky_cancellation(cancel_check)
        # Include first-use bootstrap latency, but its physical I/O is separately observed.
        with observe_read(session_id, "range") as cost:
            yield from self._snapshot(session_id, cancel_check, cost)

    def _snapshot(
        self, session_id: str, cancel_check: CancelCheck, cost: ReadCost
    ) -> Iterator[SessionRead]:
        version = self._store._history_version(session_id, cancel_check)
        cost.version = version
        path = self._store._business_index.path(session_id)
        cancelled: list[BaseException] = []

        def progress() -> int:
            cost.sql_steps_lower_bound += 1000
            try:
                check_cancel(cancel_check)
            except BaseException as exc:
                cancelled.append(exc)
                return 1
            return 0

        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
                db.row_factory = sqlite3.Row
                db.set_progress_handler(progress, 1000)
                db.execute("BEGIN")
                yield SessionRead(db, version, cancel_check, session_id, cost)
                check_cancel(cancel_check)
                if self.version(session_id) != version:
                    raise ValueError("history_changed")
        except sqlite3.Error as exc:
            if cancelled:
                raise cancelled[0] from None
            if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in {
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
                sqlite3.SQLITE_SCHEMA,
            }:
                self._store.discard_business_index(session_id)
            raise SessionPersistError("会话读取索引暂不可用，请重试") from exc
        except (SessionCorruptError, json.JSONDecodeError):
            # The caller receives an explicit failure. A later retry bootstraps
            # from the journal; never pretend a corrupt target simply vanished.
            self._store.discard_business_index(session_id)
            raise

    def messages(self, session_id: str, **kwargs: Any) -> MessagePage:
        return self._store.query_messages(session_id, **kwargs)

    def recall_text(
        self, session_id: str, *, cancel_check: CancelCheck = None, boundary_id: str | None = None
    ) -> SessionRecallSnapshot:
        """Explicit complete textual scope for recall ranking, without rollout payloads."""
        return self._store.query_recall(
            session_id,
            cancel_check=cancel_check,
            position_ids={boundary_id} if boundary_id else set(),
        )

    def event(self, session_id: str, event_id: str, *, active: bool = True) -> Event | None:
        with self.snapshot(session_id) as read:
            return read.event(event_id, active=active)

    def events_by_id(self, session_id: str, event_ids: tuple[str, ...]) -> dict[str, Event]:
        """Batch known identities in one version, including multi-message quotes."""
        identities = tuple(dict.fromkeys(event_ids))
        if not identities:
            return {}
        with self.snapshot(session_id) as read:
            result = {}
            for offset in range(0, len(identities), 1000):
                for _, event in read.events(
                    EventFilter(ids=identities[offset : offset + 1000]), limit=1000
                ):
                    result[event.uuid] = event
            return result

    def latest(
        self, session_id: str, *kinds: str, run_id: str | None = None, active: bool = True
    ) -> Event | None:
        with self.snapshot(session_id) as read:
            return read.latest(*kinds, run_id=run_id, active=active)


def session_reader(store: SessionStore) -> SessionReader:
    """Capabilities are explicit. Never hide a full load behind an adapter fallback."""
    reader = getattr(store, "reader", None)
    if reader is None:
        raise NotImplementedError(f"{type(store).__name__} does not provide scoped session reading")
    return cast(SessionReader, reader)
