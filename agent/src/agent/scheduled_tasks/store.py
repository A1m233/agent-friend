"""SQLite persistence and state transitions for Scheduled Agent Tasks."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.paths import scheduled_tasks_db_path

from ..scheduling import advance_due_window, schedule_from_dict, schedule_to_dict
from ..session_requests.models import RequestOptions
from .delivery import ScheduledNotificationStore
from .models import (
    ScheduledOccurrence,
    ScheduledOccurrenceClaim,
    ScheduledOccurrenceOutcome,
    ScheduledOccurrenceState,
    ScheduledTask,
    ScheduledTaskError,
    ScheduledTaskListQuery,
    ScheduledTaskSnapshot,
    ScheduledTaskStatus,
    ScheduledTriggerKind,
)

_NON_TERMINAL_STATES = (
    ScheduledOccurrenceState.PENDING.value,
    ScheduledOccurrenceState.CLAIMED.value,
    ScheduledOccurrenceState.SUBMITTED.value,
    ScheduledOccurrenceState.RUNNING.value,
)
_ON_TIME_GRACE = timedelta(minutes=1)


class ScheduledTaskStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or scheduled_tasks_db_path()
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.notifications = ScheduledNotificationStore(self._connect, self._transaction)

    def create_task(self, task: ScheduledTask) -> ScheduledTask:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    id, name, instruction, schedule_json, next_fire_at_utc, status,
                    persona_id, persona_name, model_id, skill_id, origin_session_id,
                    fire_count, revision, created_at, updated_at, deleted_at,
                    target_session_id, request_options_json, pause_reason, notify_bound_im
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _task_values(task),
            )
        return task

    def get_task(self, task_id: str, *, include_deleted: bool = True) -> ScheduledTask:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None or (not include_deleted and row["status"] == "deleted"):
            raise ScheduledTaskError("task_not_found", f"Scheduled Task 不存在: {task_id}")
        return _task_from_row(row)

    def list_tasks(self, query: ScheduledTaskListQuery) -> list[ScheduledTask]:
        if not query.statuses:
            return []
        placeholders = ",".join("?" for _ in query.statuses)
        params: list[object] = [status.value for status in query.statuses]
        params.extend((query.limit, query.offset))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM scheduled_tasks WHERE status IN ({placeholders})
                ORDER BY updated_at DESC, id LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def update_task(self, task: ScheduledTask, *, expected_revision: int) -> ScheduledTask:
        with self._transaction() as conn:
            current = self._require_task(conn, task.id)
            if current.revision != expected_revision:
                raise _revision_error(expected_revision, current.revision)
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks SET name = ?, instruction = ?, schedule_json = ?,
                    next_fire_at_utc = ?, status = ?, persona_id = ?, persona_name = ?,
                    model_id = ?, skill_id = ?, origin_session_id = ?, fire_count = ?,
                    revision = ?, updated_at = ?, deleted_at = ?,
                    target_session_id = ?, request_options_json = ?, pause_reason = ?, notify_bound_im = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    task.name,
                    task.instruction,
                    json.dumps(schedule_to_dict(task.schedule), sort_keys=True),
                    _dump_dt(task.next_fire_at_utc),
                    task.status.value,
                    task.persona_id,
                    task.persona_name,
                    task.model_id,
                    task.skill_id,
                    task.origin_session_id,
                    task.fire_count,
                    task.revision,
                    _dump_dt(task.updated_at),
                    _dump_dt(task.deleted_at),
                    task.target_session_id,
                    json.dumps(task.request_options.to_dict(), sort_keys=True),
                    task.pause_reason,
                    int(task.notify_bound_im),
                    task.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ScheduledTaskError("revision_conflict", "Scheduled Task 已被其他操作修改")
        return task

    def pause_for_target(self, session_id: str, reason: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET status=CASE WHEN status='active' THEN 'paused' ELSE status END,next_fire_at_utc=NULL,pause_reason=?,revision=revision+1 WHERE target_session_id=? AND status!='deleted' AND pause_reason!=?",
                (reason, session_id, reason),
            )

    def referenced_assets(self) -> set[str]:
        with self._connect() as conn:
            tasks = conn.execute(
                "SELECT request_options_json FROM scheduled_tasks WHERE status!='deleted'"
            ).fetchall()
            runs = conn.execute("SELECT task_snapshot_json FROM scheduled_occurrences").fetchall()
        values = [json.loads(row[0]) for row in tasks]
        values.extend(json.loads(row[0]).get("request_options", {}) for row in runs)
        return {ref["asset_id"] for options in values for ref in options.get("attachments", [])}

    def create_due_occurrences(
        self,
        now: datetime,
        *,
        max_lateness: timedelta = timedelta(days=7),
        limit: int = 100,
    ) -> list[str]:
        current = _as_utc(now)
        created: list[str] = []
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_tasks
                WHERE status = 'active' AND next_fire_at_utc IS NOT NULL
                    AND next_fire_at_utc <= ?
                ORDER BY next_fire_at_utc, id LIMIT ?
                """,
                (_dump_dt(current), limit),
            ).fetchall()
            for row in rows:
                task = _task_from_row(row)
                first_due = task.next_fire_at_utc
                if first_due is None:
                    continue
                window = advance_due_window(
                    task.schedule,
                    first_due,
                    current,
                    generated_count=task.fire_count,
                )
                outcome: ScheduledOccurrenceOutcome | None = None
                reason_code = ""
                state = ScheduledOccurrenceState.PENDING
                if window.latest_due_at < current - max_lateness:
                    state = ScheduledOccurrenceState.TERMINAL
                    outcome = ScheduledOccurrenceOutcome.EXPIRED
                    reason_code = "missed_beyond_lateness_window"
                elif self._has_non_terminal_occurrence(conn, task.id):
                    state = ScheduledOccurrenceState.TERMINAL
                    outcome = ScheduledOccurrenceOutcome.SKIPPED
                    reason_code = "task_run_in_progress"
                trigger = (
                    ScheduledTriggerKind.CATCH_UP
                    if window.due_count > 1 or window.latest_due_at < current - _ON_TIME_GRACE
                    else ScheduledTriggerKind.SCHEDULED
                )
                occurrence = _new_occurrence(
                    task,
                    trigger_kind=trigger,
                    scheduled_at=window.latest_due_at,
                    now=current,
                    state=state,
                    outcome=outcome,
                    reason_code=reason_code,
                    coalesced_count=max(0, window.due_count - 1),
                    coalesced_from=(window.earliest_due_at if window.due_count > 1 else None),
                )
                if self._insert_occurrence(
                    conn, occurrence, schedule_key=_schedule_key(task.id, window.latest_due_at)
                ):
                    created.append(occurrence.id)
                next_status = (
                    ScheduledTaskStatus.ACTIVE
                    if window.next_fire_at is not None
                    else ScheduledTaskStatus.EXHAUSTED
                )
                conn.execute(
                    """
                    UPDATE scheduled_tasks SET next_fire_at_utc = ?, status = ?,
                        fire_count = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        _dump_dt(window.next_fire_at),
                        next_status.value,
                        task.fire_count + window.due_count,
                        _dump_dt(current),
                        task.id,
                        task.revision,
                    ),
                )
        return created

    def create_manual_occurrence(self, task_id: str, *, now: datetime) -> ScheduledOccurrence:
        current = _as_utc(now)
        with self._transaction() as conn:
            task = self._require_task(conn, task_id)
            if task.status is ScheduledTaskStatus.DELETED:
                raise ScheduledTaskError("invalid_task_state", "已删除任务不能立即运行")
            blocked = self._has_non_terminal_occurrence(conn, task.id)
            occurrence = _new_occurrence(
                task,
                trigger_kind=ScheduledTriggerKind.MANUAL,
                scheduled_at=current,
                now=current,
                state=(
                    ScheduledOccurrenceState.TERMINAL
                    if blocked
                    else ScheduledOccurrenceState.PENDING
                ),
                outcome=ScheduledOccurrenceOutcome.SKIPPED if blocked else None,
                reason_code="task_run_in_progress" if blocked else "",
                coalesced_count=0,
                coalesced_from=None,
            )
            self._insert_occurrence(conn, occurrence, schedule_key=None)
        return occurrence

    def claim_pending(
        self,
        now: datetime,
        *,
        lease: timedelta = timedelta(seconds=30),
        limit: int = 20,
    ) -> list[ScheduledOccurrenceClaim]:
        current = _as_utc(now)
        claimed: list[ScheduledOccurrenceClaim] = []
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE scheduled_occurrences SET state = 'pending', claim_until = NULL,
                    updated_at = ? WHERE state = 'claimed' AND claim_until <= ?
                """,
                (_dump_dt(current), _dump_dt(current)),
            )
            rows = conn.execute(
                """
                SELECT * FROM scheduled_occurrences WHERE state = 'pending'
                ORDER BY scheduled_at_utc, created_at LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                snapshot = _snapshot_from_dict(json.loads(row["task_snapshot_json"]))
                if snapshot.target_session_id is None:
                    conn.execute(
                        "UPDATE scheduled_occurrences SET state='terminal',outcome='rejected',reason_code='target_confirmation_required' WHERE id=?",
                        (row["id"],),
                    )
                    continue
                session_id = snapshot.target_session_id
                run_id = row["run_id"] or uuid.uuid4().hex
                cursor = conn.execute(
                    """
                    UPDATE scheduled_occurrences SET state = 'claimed', claim_until = ?,
                        session_id = ?, run_id = ?, updated_at = ?
                    WHERE id = ? AND state = 'pending'
                    """,
                    (
                        _dump_dt(current + lease),
                        session_id,
                        run_id,
                        _dump_dt(current),
                        row["id"],
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                fresh = self._require_occurrence(conn, str(row["id"]))
                task = self._require_task(conn, fresh.task_id)
                claimed.append(ScheduledOccurrenceClaim(fresh, task))
        return claimed

    def mark_submitted(
        self,
        occurrence_id: str,
        *,
        now: datetime,
        skill_source: str | None,
        skill_version: str | None,
    ) -> ScheduledOccurrence:
        return self._transition(
            occurrence_id,
            expected=(ScheduledOccurrenceState.CLAIMED,),
            state=ScheduledOccurrenceState.SUBMITTED,
            now=now,
            skill_source=skill_source,
            skill_version=skill_version,
            clear_claim=True,
        )

    def mark_running(self, occurrence_id: str, *, now: datetime) -> ScheduledOccurrence:
        return self._transition(
            occurrence_id,
            expected=(ScheduledOccurrenceState.SUBMITTED,),
            state=ScheduledOccurrenceState.RUNNING,
            now=now,
            started_at=_as_utc(now),
        )

    def mark_terminal(
        self,
        occurrence_id: str,
        *,
        outcome: ScheduledOccurrenceOutcome,
        reason_code: str,
        now: datetime,
        started_at: datetime | None = None,
        expected: tuple[ScheduledOccurrenceState, ...] = (
            ScheduledOccurrenceState.CLAIMED,
            ScheduledOccurrenceState.SUBMITTED,
            ScheduledOccurrenceState.RUNNING,
        ),
    ) -> ScheduledOccurrence:
        return self._transition(
            occurrence_id,
            expected=expected,
            state=ScheduledOccurrenceState.TERMINAL,
            now=now,
            outcome=outcome,
            reason_code=reason_code,
            finished_at=_as_utc(now),
            started_at=started_at,
            clear_claim=True,
        )

    def recover_startup(
        self, now: datetime, terminal_lookup: Callable[[str], tuple[str, str] | None] | None = None
    ) -> dict[str, int]:
        current = _as_utc(now)
        with self._transaction() as conn:
            released = conn.execute(
                """
                UPDATE scheduled_occurrences SET state = 'pending', claim_until = NULL,
                    updated_at = ? WHERE state = 'claimed'
                """,
                (_dump_dt(current),),
            ).rowcount
            if terminal_lookup is not None:
                rows = conn.execute(
                    "SELECT id,run_id FROM scheduled_occurrences WHERE state IN ('submitted','running')"
                ).fetchall()
                for row in rows:
                    terminal = terminal_lookup(row["run_id"])
                    if terminal is not None:
                        outcome, reason = terminal
                        conn.execute(
                            "UPDATE scheduled_occurrences SET state='terminal',outcome=?,reason_code=?,claim_until=NULL,finished_at=?,updated_at=? WHERE id=?",
                            (outcome, reason, _dump_dt(current), _dump_dt(current), row["id"]),
                        )
            interrupted = conn.execute(
                """
                UPDATE scheduled_occurrences SET state = 'terminal', outcome = 'interrupted',
                    reason_code = 'bridge_restarted_after_submit', claim_until = NULL,
                    finished_at = ?, updated_at = ?
                WHERE state IN ('submitted', 'running')
                """,
                (_dump_dt(current), _dump_dt(current)),
            ).rowcount
        return {"released_claims": released, "interrupted_runs": interrupted}

    def get_occurrence(self, occurrence_id: str) -> ScheduledOccurrence:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_occurrences WHERE id = ?",
                (occurrence_id,),
            ).fetchone()
        if row is None:
            raise ScheduledTaskError("occurrence_not_found", f"Occurrence 不存在: {occurrence_id}")
        return _occurrence_from_row(row)

    def list_occurrences(
        self,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScheduledOccurrence]:
        self.get_task(task_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_occurrences WHERE task_id = ?
                ORDER BY scheduled_at_utc DESC, created_at DESC LIMIT ? OFFSET ?
                """,
                (task_id, limit, offset),
            ).fetchall()
        return [_occurrence_from_row(row) for row in rows]

    def next_wakeup_at(self) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(value) AS wake_at FROM (
                    SELECT next_fire_at_utc AS value FROM scheduled_tasks
                    WHERE status = 'active' AND next_fire_at_utc IS NOT NULL
                    UNION ALL
                    SELECT created_at AS value FROM scheduled_occurrences WHERE state = 'pending'
                    UNION ALL
                    SELECT claim_until AS value FROM scheduled_occurrences
                    WHERE state = 'claimed' AND claim_until IS NOT NULL
                )
                """
            ).fetchone()
        return _load_dt(row["wake_at"]) if row and row["wake_at"] else None

    def _transition(
        self,
        occurrence_id: str,
        *,
        expected: tuple[ScheduledOccurrenceState, ...],
        state: ScheduledOccurrenceState,
        now: datetime,
        outcome: ScheduledOccurrenceOutcome | None = None,
        reason_code: str | None = None,
        skill_source: str | None = None,
        skill_version: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        clear_claim: bool = False,
    ) -> ScheduledOccurrence:
        current_time = _as_utc(now)
        with self._transaction() as conn:
            current = self._require_occurrence(conn, occurrence_id)
            if current.state not in expected:
                if current.state is state and current.outcome == outcome:
                    return current
                raise ScheduledTaskError(
                    "invalid_occurrence_state",
                    f"Occurrence 当前状态 {current.state.value} 不能迁移到 {state.value}",
                )
            conn.execute(
                """
                UPDATE scheduled_occurrences SET state = ?, outcome = ?, reason_code = ?,
                    claim_until = ?, skill_source = ?, skill_version = ?, started_at = ?,
                    finished_at = ?, updated_at = ? WHERE id = ?
                """,
                (
                    state.value,
                    outcome.value
                    if outcome
                    else current.outcome.value
                    if current.outcome
                    else None,
                    reason_code if reason_code is not None else current.reason_code,
                    None if clear_claim else _dump_dt(current.claim_until),
                    skill_source if skill_source is not None else current.skill_source,
                    skill_version if skill_version is not None else current.skill_version,
                    _dump_dt(started_at or current.started_at),
                    _dump_dt(finished_at or current.finished_at),
                    _dump_dt(current_time),
                    occurrence_id,
                ),
            )
        return self.get_occurrence(occurrence_id)

    def _insert_occurrence(
        self,
        conn: sqlite3.Connection,
        occurrence: ScheduledOccurrence,
        *,
        schedule_key: str | None,
    ) -> bool:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO scheduled_occurrences (
                id, task_id, task_revision, trigger_kind, scheduled_at_utc,
                task_snapshot_json, schedule_key, state, outcome, reason_code,
                coalesced_count, coalesced_from_utc, claim_until, session_id, run_id,
                skill_source, skill_version, created_at, updated_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurrence.id,
                occurrence.task_id,
                occurrence.task_revision,
                occurrence.trigger_kind.value,
                _dump_dt(occurrence.scheduled_at_utc),
                json.dumps(_snapshot_to_dict(occurrence.task_snapshot), sort_keys=True),
                schedule_key,
                occurrence.state.value,
                occurrence.outcome.value if occurrence.outcome else None,
                occurrence.reason_code,
                occurrence.coalesced_count,
                _dump_dt(occurrence.coalesced_from_utc),
                _dump_dt(occurrence.claim_until),
                occurrence.session_id,
                occurrence.run_id,
                occurrence.skill_source,
                occurrence.skill_version,
                _dump_dt(occurrence.created_at),
                _dump_dt(occurrence.updated_at),
                _dump_dt(occurrence.started_at),
                _dump_dt(occurrence.finished_at),
            ),
        )
        inserted = cursor.rowcount == 1
        if inserted and occurrence.task_snapshot.notify_bound_im:
            ScheduledNotificationStore.register(conn, occurrence.id)
        return inserted

    def _has_non_terminal_occurrence(self, conn: sqlite3.Connection, task_id: str) -> bool:
        placeholders = ",".join("?" for _ in _NON_TERMINAL_STATES)
        row = conn.execute(
            f"SELECT 1 FROM scheduled_occurrences WHERE task_id = ? "
            f"AND state IN ({placeholders}) LIMIT 1",
            (task_id, *_NON_TERMINAL_STATES),
        ).fetchone()
        return row is not None

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    next_fire_at_utc TEXT,
                    status TEXT NOT NULL CHECK (status IN ('active','paused','exhausted','deleted')),
                    persona_id TEXT,
                    persona_name TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    skill_id TEXT,
                    origin_session_id TEXT,
                    fire_count INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
                    ON scheduled_tasks(status, next_fire_at_utc);

                CREATE TABLE IF NOT EXISTS scheduled_occurrences (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES scheduled_tasks(id),
                    task_revision INTEGER NOT NULL,
                    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('scheduled','catch_up','manual')),
                    scheduled_at_utc TEXT NOT NULL,
                    task_snapshot_json TEXT NOT NULL,
                    schedule_key TEXT UNIQUE,
                    state TEXT NOT NULL CHECK (state IN ('pending','claimed','submitted','running','terminal')),
                    outcome TEXT CHECK (outcome IS NULL OR outcome IN (
                        'completed','failed','cancelled','skipped','expired','interrupted','rejected'
                    )),
                    reason_code TEXT NOT NULL,
                    coalesced_count INTEGER NOT NULL,
                    coalesced_from_utc TEXT,
                    claim_until TEXT,
                    session_id TEXT,
                    run_id TEXT,
                    skill_source TEXT,
                    skill_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_occurrences_pending
                    ON scheduled_occurrences(state, scheduled_at_utc);
                CREATE INDEX IF NOT EXISTS idx_scheduled_occurrences_task
                    ON scheduled_occurrences(task_id, scheduled_at_utc DESC);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(scheduled_tasks)")}
            if "notify_bound_im" not in columns:
                conn.execute(
                    "ALTER TABLE scheduled_tasks ADD COLUMN notify_bound_im INTEGER NOT NULL DEFAULT 0"
                )
            ScheduledNotificationStore.initialize(conn)
            if "target_session_id" not in columns:
                conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN target_session_id TEXT")
                conn.execute(
                    "ALTER TABLE scheduled_tasks ADD COLUMN request_options_json TEXT NOT NULL DEFAULT '{}'"
                )
                conn.execute(
                    "ALTER TABLE scheduled_tasks ADD COLUMN pause_reason TEXT NOT NULL DEFAULT ''"
                )
                for row in conn.execute("SELECT id,model_id FROM scheduled_tasks").fetchall():
                    options = RequestOptions.from_dict({"model": {"model_id": row["model_id"]}})
                    conn.execute(
                        "UPDATE scheduled_tasks SET request_options_json=?,pause_reason='target_confirmation_required',status=CASE WHEN status='active' THEN 'paused' ELSE status END,next_fire_at_utc=NULL WHERE id=?",
                        (json.dumps(options.to_dict()), row["id"]),
                    )
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    @staticmethod
    def _require_task(conn: sqlite3.Connection, task_id: str) -> ScheduledTask:
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ScheduledTaskError("task_not_found", f"Scheduled Task 不存在: {task_id}")
        return _task_from_row(row)

    @staticmethod
    def _require_occurrence(conn: sqlite3.Connection, occurrence_id: str) -> ScheduledOccurrence:
        row = conn.execute(
            "SELECT * FROM scheduled_occurrences WHERE id = ?", (occurrence_id,)
        ).fetchone()
        if row is None:
            raise ScheduledTaskError("occurrence_not_found", f"Occurrence 不存在: {occurrence_id}")
        return _occurrence_from_row(row)


def _new_occurrence(
    task: ScheduledTask,
    *,
    trigger_kind: ScheduledTriggerKind,
    scheduled_at: datetime,
    now: datetime,
    state: ScheduledOccurrenceState,
    outcome: ScheduledOccurrenceOutcome | None,
    reason_code: str,
    coalesced_count: int,
    coalesced_from: datetime | None,
) -> ScheduledOccurrence:
    current = _as_utc(now)
    snapshot = ScheduledTaskSnapshot(
        task_id=task.id,
        task_revision=task.revision,
        name=task.name,
        instruction=task.instruction,
        schedule=task.schedule,
        persona_id=task.persona_id,
        persona_name=task.persona_name,
        model_id=task.model_id,
        skill_id=task.skill_id,
        target_session_id=task.target_session_id,
        request_options=task.request_options,
        notify_bound_im=task.notify_bound_im,
    )
    terminal_at = current if state is ScheduledOccurrenceState.TERMINAL else None
    return ScheduledOccurrence(
        id=str(uuid.uuid4()),
        task_id=task.id,
        task_revision=task.revision,
        trigger_kind=trigger_kind,
        scheduled_at_utc=_as_utc(scheduled_at),
        task_snapshot=snapshot,
        state=state,
        outcome=outcome,
        reason_code=reason_code,
        coalesced_count=coalesced_count,
        coalesced_from_utc=coalesced_from,
        claim_until=None,
        session_id=None,
        run_id=None,
        skill_source=None,
        skill_version=None,
        created_at=current,
        updated_at=current,
        finished_at=terminal_at,
    )


def _task_values(task: ScheduledTask) -> tuple[object, ...]:
    return (
        task.id,
        task.name,
        task.instruction,
        json.dumps(schedule_to_dict(task.schedule), sort_keys=True),
        _dump_dt(task.next_fire_at_utc),
        task.status.value,
        task.persona_id,
        task.persona_name,
        task.model_id,
        task.skill_id,
        task.origin_session_id,
        task.fire_count,
        task.revision,
        _dump_dt(task.created_at),
        _dump_dt(task.updated_at),
        _dump_dt(task.deleted_at),
        task.target_session_id,
        json.dumps(task.request_options.to_dict(), sort_keys=True),
        task.pause_reason,
        int(task.notify_bound_im),
    )


def _task_from_row(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        id=str(row["id"]),
        name=str(row["name"]),
        instruction=str(row["instruction"]),
        schedule=schedule_from_dict(json.loads(str(row["schedule_json"]))),
        next_fire_at_utc=_load_dt(row["next_fire_at_utc"]),
        status=ScheduledTaskStatus(str(row["status"])),
        persona_id=str(row["persona_id"]) if row["persona_id"] is not None else None,
        persona_name=str(row["persona_name"]),
        model_id=str(row["model_id"]),
        skill_id=str(row["skill_id"]) if row["skill_id"] is not None else None,
        origin_session_id=(
            str(row["origin_session_id"]) if row["origin_session_id"] is not None else None
        ),
        fire_count=int(row["fire_count"]),
        revision=int(row["revision"]),
        created_at=_required_dt(row["created_at"]),
        updated_at=_required_dt(row["updated_at"]),
        deleted_at=_load_dt(row["deleted_at"]),
        target_session_id=row["target_session_id"],
        request_options=RequestOptions.from_dict(json.loads(row["request_options_json"])),
        pause_reason=str(row["pause_reason"]),
        notify_bound_im=bool(row["notify_bound_im"]),
    )


def _occurrence_from_row(row: sqlite3.Row) -> ScheduledOccurrence:
    snapshot = _snapshot_from_dict(json.loads(str(row["task_snapshot_json"])))
    raw_outcome = row["outcome"]
    return ScheduledOccurrence(
        id=str(row["id"]),
        task_id=str(row["task_id"]),
        task_revision=int(row["task_revision"]),
        trigger_kind=ScheduledTriggerKind(str(row["trigger_kind"])),
        scheduled_at_utc=_required_dt(row["scheduled_at_utc"]),
        task_snapshot=snapshot,
        state=ScheduledOccurrenceState(str(row["state"])),
        outcome=(ScheduledOccurrenceOutcome(str(raw_outcome)) if raw_outcome else None),
        reason_code=str(row["reason_code"]),
        coalesced_count=int(row["coalesced_count"]),
        coalesced_from_utc=_load_dt(row["coalesced_from_utc"]),
        claim_until=_load_dt(row["claim_until"]),
        session_id=str(row["session_id"]) if row["session_id"] is not None else None,
        run_id=str(row["run_id"]) if row["run_id"] is not None else None,
        skill_source=(str(row["skill_source"]) if row["skill_source"] is not None else None),
        skill_version=(str(row["skill_version"]) if row["skill_version"] is not None else None),
        created_at=_required_dt(row["created_at"]),
        updated_at=_required_dt(row["updated_at"]),
        started_at=_load_dt(row["started_at"]),
        finished_at=_load_dt(row["finished_at"]),
    )


def _snapshot_to_dict(snapshot: ScheduledTaskSnapshot) -> dict[str, Any]:
    return {
        "task_id": snapshot.task_id,
        "task_revision": snapshot.task_revision,
        "name": snapshot.name,
        "instruction": snapshot.instruction,
        "schedule": schedule_to_dict(snapshot.schedule),
        "persona_id": snapshot.persona_id,
        "persona_name": snapshot.persona_name,
        "model_id": snapshot.model_id,
        "skill_id": snapshot.skill_id,
        "target_session_id": snapshot.target_session_id,
        "request_options": snapshot.request_options.to_dict(),
        "notify_bound_im": snapshot.notify_bound_im,
    }


def _snapshot_from_dict(value: dict[str, Any]) -> ScheduledTaskSnapshot:
    return ScheduledTaskSnapshot(
        task_id=str(value["task_id"]),
        task_revision=int(value["task_revision"]),
        name=str(value["name"]),
        instruction=str(value["instruction"]),
        schedule=schedule_from_dict(dict(value["schedule"])),
        persona_id=str(value["persona_id"]) if value.get("persona_id") else None,
        persona_name=str(value["persona_name"]),
        model_id=str(value["model_id"]),
        skill_id=str(value["skill_id"]) if value.get("skill_id") else None,
        target_session_id=value.get("target_session_id"),
        request_options=RequestOptions.from_dict(value.get("request_options", {})),
        notify_bound_im=value.get("notify_bound_im") is True,
    )


def _schedule_key(task_id: str, scheduled_at: datetime) -> str:
    return f"{task_id}:{_dump_dt(scheduled_at)}"


def _revision_error(expected: int, actual: int) -> ScheduledTaskError:
    return ScheduledTaskError(
        "revision_conflict",
        f"Scheduled Task revision 冲突：期望 {expected}，实际 {actual}",
        {"expected_revision": expected, "actual_revision": actual},
    )


def _dump_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _load_dt(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def _required_dt(value: object) -> datetime:
    parsed = _load_dt(value)
    if parsed is None:
        raise ScheduledTaskError("store_corrupt", "Scheduled Task 数据缺少时间字段")
    return parsed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ScheduledTaskError("invalid_datetime", "日期时间必须包含时区")
    return value.astimezone(UTC)
