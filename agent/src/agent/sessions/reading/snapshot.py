"""Version-bound SQL selections and lightweight result projections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace

from ..continuation import ContinuationTarget, continuation_target
from ..errors import SessionCorruptError
from ..events import Event
from ..query import CancelCheck, check_cancel
from ..store_types import SessionLoadDiagnostics, SourceVersion
from .observation import ReadCost
from .types import EventFilter


def _where(selection: EventFilter) -> tuple[str, list[object]]:
    clauses = ["position>?"]
    params: list[object] = [selection.after]
    if selection.active:
        clauses.append("active=1")
    if selection.before is not None:
        clauses.append("position<?")
        params.append(selection.before)
    for column, values in (("kind", selection.kinds), ("uuid", selection.ids)):
        if values is not None:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    for path, value in (
        ("meta.run_id", selection.run_id),
        ("payload.evidence_ref", selection.evidence_ref),
        ("payload.event_tool_call_id", selection.event_tool_call_id),
        ("payload.tool_call_id", selection.tool_call_id),
    ):
        if value is not None:
            clauses.append(f"json_extract(body,'$.{path}')=?")
            params.append(value)
    if selection.attachments:
        clauses.append("json_array_length(body,'$.payload.attachments')>0")
    if selection.exclude_tools:
        clauses.append(
            f"COALESCE(json_extract(body,'$.payload.tool_name'),'') NOT IN ({','.join('?' for _ in selection.exclude_tools)})"
        )
        params.extend(selection.exclude_tools)
    return " AND ".join(clauses), params


class SessionRead:
    """One short read transaction. All IDs, pairs and ranges share this version."""

    def __init__(
        self,
        db: sqlite3.Connection,
        version: SourceVersion,
        cancel: CancelCheck,
        session_id: str,
        cost: ReadCost,
    ) -> None:
        self.cost = cost
        self._db = db
        self._cancel = cancel
        self.source_version = version
        state = db.execute("SELECT * FROM state WHERE id=1").fetchone()
        if state is None or tuple(json.loads(state["version"])) != version:
            raise ValueError("history_changed")
        if json.loads(state["metadata"])["session_id"] != session_id:
            raise SessionCorruptError("session index identity mismatch")
        self.event_count = int(state["count"])
        self.diagnostics = SessionLoadDiagnostics(**json.loads(state["diagnostics"]))
        self.history_epoch = str(json.loads(state["metadata"])["history_epoch"])

    def events(
        self, selection: EventFilter, *, limit: int = 100, reverse: bool = False
    ) -> tuple[tuple[int, Event], ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("event limit must be between 1 and 1000")
        check_cancel(self._cancel)
        if self.cost.mode != "stream":
            self.cost.mode = (
                "point" if selection.ids is not None or selection.evidence_ref else "range"
            )
        self.cost.requested_ids += len(selection.ids or ())
        self.cost.requested_limit += limit
        where, params = _where(selection)
        order = "DESC" if reverse else "ASC"
        rows = self._db.execute(
            f"SELECT position,body FROM events WHERE {where} ORDER BY position {order} LIMIT ?",
            (*params, limit),
        )
        result = []
        for position, body in rows:
            check_cancel(self._cancel)
            self.cost.rows += 1
            self.cost.body_chars += len(body)
            result.append((position, Event.from_jsonl(body)))
        return tuple(result)

    def contains(self, selection: EventFilter) -> bool:
        check_cancel(self._cancel)
        where, params = _where(selection)
        return (
            self._db.execute(f"SELECT 1 FROM events WHERE {where} LIMIT 1", params).fetchone()
            is not None
        )

    def has_attachment(self, asset_id: str) -> bool:
        check_cancel(self._cancel)
        return (
            self._db.execute(
                "SELECT 1 FROM attachment_refs r JOIN events e ON e.uuid=r.uuid WHERE r.asset_id=? AND e.active=1 AND (e.kind='user_message' OR json_type(e.body,'$.payload.generated_image') IS NOT NULL) LIMIT 1",
                (asset_id,),
            ).fetchone()
            is not None
        )

    def has_asset(self, asset_id: str) -> bool:
        check_cancel(self._cancel)
        return (
            self._db.execute(
                "SELECT 1 FROM asset_refs r JOIN events e ON e.uuid=r.uuid WHERE r.asset_id=? AND e.active=1 LIMIT 1",
                (asset_id,),
            ).fetchone()
            is not None
        )

    def attachments(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        file_ref: str | None = None,
        message_id: str | None = None,
        image_only: bool = False,
        reverse: bool = False,
        count: bool = False,
    ) -> tuple[list[tuple[str, str, str, object]], int | None]:
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid attachment range")
        check_cancel(self._cancel)
        where, params = ["e.active=1"], []
        for column, value in (("r.file_ref", file_ref), ("r.uuid", message_id)):
            if value is not None:
                where.append(f"{column}=?")
                params.append(value)
        if image_only:
            where.append("r.category='image'")
        query = "FROM attachment_refs r JOIN events e ON e.uuid=r.uuid WHERE " + " AND ".join(where)
        total = (
            self._db.execute("SELECT COUNT(*) " + query, params).fetchone()[0] if count else None
        )
        order = "DESC" if reverse else "ASC"
        rows = self._db.execute(
            "SELECT r.file_ref,r.uuid,e.kind,r.body "
            + query
            + f" ORDER BY r.position {order},r.ordinal {order} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        self.cost.mode = "point" if file_ref or message_id else "range"
        self.cost.requested_limit += limit
        result = []
        for ref, uid, kind, body in rows:
            self.cost.rows += 1
            self.cost.body_chars += len(body)
            result.append((ref, uid, kind, json.loads(body)))
        check_cancel(self._cancel)
        return result, total

    def positions(self, selection: EventFilter) -> dict[str, int]:
        """ID/order projection, without reading or materializing payloads."""
        where, params = _where(selection)
        result = {}
        for uid, position in self._db.execute(
            f"SELECT uuid,position FROM events WHERE {where} ORDER BY position", params
        ):
            check_cancel(self._cancel)
            self.cost.rows += 1
            result[str(uid)] = int(position)
        return result

    def scan(self, selection: EventFilter, *, purpose: str) -> Iterator[tuple[int, Event]]:
        """Stream a declared scope (e.g. full text search), never collect an entire Session."""
        if not purpose or (
            selection.kinds is None
            and selection.ids is None
            and selection.before is None
            and selection.run_id is None
            and not selection.attachments
        ):
            raise ValueError("streaming reads require a purpose and an explicit scope")
        self.cost.mode = "stream"
        while batch := self.events(selection):
            yield from batch
            selection = replace(selection, after=batch[-1][0])

    def event(self, event_id: str, *, active: bool = True) -> Event | None:
        rows = self.events(EventFilter(ids=(event_id,), active=active), limit=1)
        return rows[0][1] if rows else None

    def latest(self, *kinds: str, run_id: str | None = None, active: bool = True) -> Event | None:
        rows = self.events(
            EventFilter(kinds=kinds, run_id=run_id, active=active), limit=1, reverse=True
        )
        return rows[0][1] if rows else None

    def continuation(self, source_run_id: str | None = None) -> ContinuationTarget:
        boundary = self.events(
            EventFilter(kinds=("user_message", "system_trigger")), limit=1, reverse=True
        )
        facts = []
        if boundary:
            for (body,) in self._db.execute(
                "SELECT recovery FROM events WHERE active=1 AND position>=? AND recovery IS NOT NULL ORDER BY position",
                (boundary[0][0],),
            ):
                check_cancel(self._cancel)
                facts.append(Event.from_jsonl(body))
        target = continuation_target(facts, source_run_id)
        assert boundary
        return replace(target, user_event=boundary[0][1])

    def search_events(self) -> Iterator[Event]:
        for (body,) in self._db.execute(
            "SELECT v.body FROM events e JOIN text_views v ON e.uuid=v.uuid WHERE e.active=1 ORDER BY e.position"
        ):
            check_cancel(self._cancel)
            self.cost.rows += 1
            self.cost.body_chars += len(body)
            yield Event.from_jsonl(body)

    def conversation_ids(self) -> tuple[str, ...]:
        result = []
        for (uid,) in self._db.execute(
            "SELECT e.uuid FROM events e JOIN text_views v ON e.uuid=v.uuid WHERE e.active=1 AND v.recall=1 ORDER BY e.position"
        ):
            check_cancel(self._cancel)
            self.cost.rows += 1
            result.append(str(uid))
        return tuple(result)

    def inactive_in_range(self, start: int, end: int) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM journal_positions p JOIN inactive i ON p.uuid=i.uuid WHERE p.position>=? AND p.position<=? LIMIT 1",
                (start, end),
            ).fetchone()
            is not None
        )

    def source_range(self, first: str, last: str) -> tuple[int, int] | None:
        rows = dict(
            self._db.execute(
                "SELECT uuid,position FROM journal_positions WHERE uuid IN (?,?)", (first, last)
            )
        )
        if first not in rows or last not in rows or rows[first] > rows[last]:
            return None
        return int(rows[first]), int(rows[last])
