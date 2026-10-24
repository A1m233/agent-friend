"""Todo SQLite store：事务化生命周期、到期 occurrence 与恢复。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.paths import todos_db_path

from .history import UNKNOWN_ACTOR, TodoActor, append_change, history_item, latest_change, snapshot
from .models import (
    AgentResponseStatus,
    DeliveryUpdate,
    OccurrenceState,
    Reminder,
    ReminderDelivery,
    ReminderDeliveryState,
    ReminderFacts,
    ReminderKind,
    ReminderNotificationMode,
    ReminderOccurrence,
    ReminderProjectionKind,
    ReminderStatus,
    Todo,
    TodoDetails,
    TodoError,
    TodoListQuery,
    TodoStatus,
)
from .recurrence import (
    next_fire_after,
    schedule_from_dict,
    schedule_summary,
    schedule_to_dict,
    to_local,
)


class TodoStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or todos_db_path()
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_todo(
        self,
        todo: Todo,
        *,
        reminders: tuple[Reminder, ...] = (),
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Todo:
        with self._transaction(
            todo_id=todo.id, action="created", actor=actor, now=todo.created_at
        ) as conn:
            conn.execute(
                """
                INSERT INTO todos (
                    id, title, description, status, created_at, updated_at,
                    completed_at, cancelled_at, revision, origin_kind, origin_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    todo.id,
                    todo.title,
                    todo.description,
                    todo.status.value,
                    _dump_dt(todo.created_at),
                    _dump_dt(todo.updated_at),
                    _dump_dt(todo.completed_at),
                    _dump_dt(todo.cancelled_at),
                    todo.revision,
                    todo.origin_kind,
                    todo.origin_id,
                ),
            )
            for reminder in reminders:
                self._insert_reminder(conn, reminder)
        return todo

    def get_todo(self, todo_id: str) -> TodoDetails:
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            if row is None:
                raise TodoError("not_found", f"Todo 不存在: {todo_id}")
            reminder_rows = conn.execute(
                "SELECT * FROM reminders WHERE todo_id = ? ORDER BY created_at, id",
                (todo_id,),
            ).fetchall()
            latest = latest_change(conn, todo_id)
        return TodoDetails(
            _todo_from_row(row), tuple(_reminder_from_row(r) for r in reminder_rows), latest
        )

    def list_todos(self, query: TodoListQuery) -> list[TodoDetails]:
        if query.limit < 1 or query.limit > 500 or query.offset < 0:
            raise TodoError("invalid_arguments", "limit 必须为 1-500，offset 不能为负数")
        if not query.statuses:
            return []
        placeholders = ",".join("?" for _ in query.statuses)
        values: list[Any] = [status.value for status in query.statuses]
        values.extend([query.search.strip(), query.search.strip(), query.limit, query.offset])
        with self._connect() as conn:
            conn.execute("BEGIN")
            rows = conn.execute(
                f"""
                SELECT * FROM todos
                WHERE status IN ({placeholders})
                    AND (instr(lower(title), lower(?)) > 0 OR instr(lower(description), lower(?)) > 0)
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, updated_at DESC, id
                LIMIT ? OFFSET ?
                """,
                values,
            ).fetchall()
            result: list[TodoDetails] = []
            for row in rows:
                reminder_rows = conn.execute(
                    "SELECT * FROM reminders WHERE todo_id = ? ORDER BY created_at, id",
                    (row["id"],),
                ).fetchall()
                result.append(
                    TodoDetails(
                        _todo_from_row(row),
                        tuple(_reminder_from_row(item) for item in reminder_rows),
                        latest_change(conn, str(row["id"])),
                    )
                )
        return result

    def history(
        self, todo_id: str, *, before_id: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or (before_id is not None and before_id < 1):
            raise TodoError("invalid_arguments", "历史 limit 必须为 1-100，游标必须为正数")
        with self._connect() as conn:
            self._require_todo(conn, todo_id)
            rows = conn.execute(
                """SELECT * FROM todo_history WHERE todo_id = ? AND (? IS NULL OR id < ?)
                ORDER BY id DESC LIMIT ?""",
                (todo_id, before_id, before_id, limit),
            ).fetchall()
        return [history_item(row) for row in rows]

    def update_todo_fields(
        self,
        todo_id: str,
        *,
        revision: int,
        title: str,
        description: str,
        now: datetime,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Todo:
        with self._transaction(todo_id=todo_id, action="updated", actor=actor, now=now) as conn:
            current = self._require_todo(conn, todo_id)
            self._require_revision(current, revision)
            if (title, description) == (current.title, current.description):
                return current
            next_revision = current.revision + 1
            conn.execute(
                """
                UPDATE todos SET title = ?, description = ?, updated_at = ?, revision = ?
                WHERE id = ? AND revision = ?
                """,
                (title, description, _dump_dt(now), next_revision, todo_id, revision),
            )
        return self.get_todo(todo_id).todo

    def set_todo_status(
        self,
        todo_id: str,
        *,
        revision: int,
        status: TodoStatus,
        now: datetime,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Todo:
        with self._transaction(
            todo_id=todo_id, action="status_changed", actor=actor, now=now
        ) as conn:
            current = self._require_todo(conn, todo_id)
            self._require_revision(current, revision)
            if current.status == status:
                return current
            completed_at = now if status is TodoStatus.COMPLETED else None
            cancelled_at = now if status is TodoStatus.CANCELLED else None
            conn.execute(
                """
                UPDATE todos SET status = ?, completed_at = ?, cancelled_at = ?,
                    updated_at = ?, revision = ? WHERE id = ? AND revision = ?
                """,
                (
                    status.value,
                    _dump_dt(completed_at),
                    _dump_dt(cancelled_at),
                    _dump_dt(now),
                    current.revision + 1,
                    todo_id,
                    revision,
                ),
            )
            if status is not TodoStatus.OPEN:
                conn.execute(
                    """
                    UPDATE reminders SET status = 'disabled', next_fire_at_utc = NULL,
                        updated_at = ?, revision = revision + 1
                    WHERE todo_id = ? AND status = 'active'
                    """,
                    (_dump_dt(now), todo_id),
                )
                self._invalidate_occurrences(
                    conn,
                    todo_id=todo_id,
                    now=now,
                    reason=f"todo_{status.value}",
                )
        return self.get_todo(todo_id).todo

    def create_reminder(self, reminder: Reminder, *, actor: TodoActor = UNKNOWN_ACTOR) -> Reminder:
        with self._transaction(
            todo_id=reminder.todo_id,
            action="reminder_added",
            actor=actor,
            now=reminder.created_at,
        ) as conn:
            todo = self._require_todo(conn, reminder.todo_id)
            if todo.status is not TodoStatus.OPEN:
                raise TodoError("invalid_state", "只能给 open Todo 添加 Reminder")
            self._insert_reminder(conn, reminder)
        return reminder

    @staticmethod
    def _insert_reminder(conn: sqlite3.Connection, reminder: Reminder) -> None:
        conn.execute(
            """
            INSERT INTO reminders (
                id, todo_id, kind, timezone, schedule_json, next_fire_at_utc,
                status, fire_count, revision, created_at, updated_at, origin_session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reminder.id,
                reminder.todo_id,
                reminder.kind.value,
                reminder.timezone,
                json.dumps(schedule_to_dict(reminder.schedule), sort_keys=True),
                _dump_dt(reminder.next_fire_at_utc),
                reminder.status.value,
                reminder.fire_count,
                reminder.revision,
                _dump_dt(reminder.created_at),
                _dump_dt(reminder.updated_at),
                reminder.origin_session_id,
            ),
        )

    def update_reminder(
        self,
        reminder: Reminder,
        *,
        expected_revision: int,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Reminder:
        with self._transaction(
            todo_id=reminder.todo_id,
            action="reminder_updated",
            actor=actor,
            now=reminder.updated_at,
        ) as conn:
            todo = self._require_todo(conn, reminder.todo_id)
            if todo.status is not TodoStatus.OPEN and reminder.status is ReminderStatus.ACTIVE:
                raise TodoError("invalid_state", "请先重新打开待办，再启用提醒")
            current = self._require_reminder(conn, reminder.id)
            if current.todo_id != reminder.todo_id:
                raise TodoError("not_found", f"Reminder 不属于 Todo: {reminder.todo_id}")
            if current.revision != expected_revision:
                raise _revision_error(expected_revision, current.revision)
            if current.schedule == reminder.schedule and current.status == reminder.status:
                return current
            cursor = conn.execute(
                """
                UPDATE reminders SET kind = ?, timezone = ?, schedule_json = ?,
                    next_fire_at_utc = ?, status = ?, fire_count = ?, revision = ?, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    reminder.kind.value,
                    reminder.timezone,
                    json.dumps(schedule_to_dict(reminder.schedule), sort_keys=True),
                    _dump_dt(reminder.next_fire_at_utc),
                    reminder.status.value,
                    reminder.fire_count,
                    reminder.revision,
                    _dump_dt(reminder.updated_at),
                    reminder.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise TodoError("revision_conflict", "Reminder 已被其他操作修改")
            self._invalidate_occurrences(
                conn,
                reminder_id=reminder.id,
                now=reminder.updated_at,
                reason="reminder_updated",
            )
        return reminder

    def disable_reminder(
        self,
        todo_id: str,
        reminder_id: str,
        *,
        revision: int,
        now: datetime,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Reminder:
        with self._transaction(
            todo_id=todo_id, action="reminder_removed", actor=actor, now=now
        ) as conn:
            current = self._require_reminder(conn, reminder_id)
            if current.todo_id != todo_id:
                raise TodoError("not_found", f"Reminder 不属于 Todo: {todo_id}")
            if current.revision != revision:
                raise _revision_error(revision, current.revision)
            conn.execute(
                """
                UPDATE reminders SET status = 'disabled', next_fire_at_utc = NULL,
                    revision = ?, updated_at = ? WHERE id = ? AND revision = ?
                """,
                (revision + 1, _dump_dt(now), reminder_id, revision),
            )
            self._invalidate_occurrences(
                conn,
                reminder_id=reminder_id,
                now=now,
                reason="reminder_disabled",
            )
        return self.get_reminder(reminder_id)

    def get_reminder(self, reminder_id: str) -> Reminder:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if row is None:
            raise TodoError("not_found", f"Reminder 不存在: {reminder_id}")
        return _reminder_from_row(row)

    def create_due_occurrences(self, now: datetime, *, limit: int = 100) -> list[str]:
        """原子生成到期 occurrence，并把 Reminder 推进到未来或 exhausted。"""
        now = _as_utc(now)
        created: list[str] = []
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT r.* FROM reminders r
                JOIN todos t ON t.id = r.todo_id
                WHERE r.status = 'active' AND r.next_fire_at_utc IS NOT NULL
                    AND r.next_fire_at_utc <= ? AND t.status = 'open'
                ORDER BY r.next_fire_at_utc, r.id LIMIT ?
                """,
                (_dump_dt(now), limit),
            ).fetchall()
            for row in rows:
                reminder = _reminder_from_row(row)
                todo = self._require_todo(conn, reminder.todo_id)
                scheduled = reminder.next_fire_at_utc
                if scheduled is None:
                    continue
                latest = scheduled
                if reminder.kind is ReminderKind.RECURRING:
                    while True:
                        candidate = next_fire_after(
                            reminder.schedule,
                            latest,
                            generated_count=reminder.fire_count,
                        )
                        if candidate is None or candidate > now:
                            break
                        latest = candidate
                    next_fire = next_fire_after(
                        reminder.schedule,
                        latest,
                        generated_count=reminder.fire_count + 1,
                    )
                else:
                    next_fire = None
                occurrence_id = str(uuid.uuid4())
                facts = ReminderFacts(
                    occurrence_id=occurrence_id,
                    todo_id=todo.id,
                    reminder_id=reminder.id,
                    title=todo.title,
                    description=todo.description,
                    scheduled_at_utc=latest,
                    scheduled_local=to_local(latest, reminder.timezone),
                    timezone=reminder.timezone,
                    recurrence=schedule_summary(reminder.schedule),
                    todo_status=todo.status,
                )
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO reminder_occurrences (
                        id, reminder_id, todo_id, scheduled_at_utc, facts_json, state,
                        destination_kind, destination_id, delivery_receipt,
                        agent_response_status, attempt_count, next_attempt_at, claim_until,
                        created_at, updated_at, invalidated_at, invalidation_reason
                    ) VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, 'pending', 0,
                        NULL, NULL, ?, ?, NULL, NULL)
                    """,
                    (
                        occurrence_id,
                        reminder.id,
                        todo.id,
                        _dump_dt(latest),
                        json.dumps(_facts_to_dict(facts), sort_keys=True),
                        _dump_dt(now),
                        _dump_dt(now),
                    ),
                )
                if cursor.rowcount == 1:
                    created.append(occurrence_id)
                status = (
                    ReminderStatus.ACTIVE if next_fire is not None else ReminderStatus.EXHAUSTED
                )
                before = snapshot(conn, todo.id) if status is ReminderStatus.EXHAUSTED else None
                conn.execute(
                    """
                    UPDATE reminders SET next_fire_at_utc = ?, status = ?, fire_count = ?,
                        revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?
                    """,
                    (
                        _dump_dt(next_fire),
                        status.value,
                        reminder.fire_count + (1 if cursor.rowcount == 1 else 0),
                        _dump_dt(now),
                        reminder.id,
                        reminder.revision,
                    ),
                )
                if before is not None:
                    append_change(
                        conn,
                        todo_id=todo.id,
                        action="reminder_exhausted",
                        actor=TodoActor("system"),
                        occurred_at=_dump_dt(now) or "",
                        before=before,
                        after=snapshot(conn, todo.id),
                    )
        return created

    def claim_pending(
        self,
        now: datetime,
        *,
        lease: timedelta = timedelta(seconds=30),
        limit: int = 20,
    ) -> list[ReminderOccurrence]:
        now = _as_utc(now)
        claim_until = now + lease
        claimed: list[ReminderOccurrence] = []
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE reminder_occurrences SET state = 'pending', claim_until = NULL,
                    updated_at = ? WHERE state = 'claimed' AND claim_until <= ?
                """,
                (_dump_dt(now), _dump_dt(now)),
            )
            rows = conn.execute(
                """
                SELECT * FROM reminder_occurrences
                WHERE state IN ('pending', 'failed')
                    AND invalidated_at IS NULL
                    AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY scheduled_at_utc, created_at LIMIT ?
                """,
                (_dump_dt(now), limit),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE reminder_occurrences SET state = 'claimed', claim_until = ?,
                        attempt_count = attempt_count + 1, updated_at = ?
                    WHERE id = ? AND state IN ('pending', 'failed')
                        AND invalidated_at IS NULL
                    """,
                    (_dump_dt(claim_until), _dump_dt(now), row["id"]),
                )
                if cursor.rowcount == 1:
                    fresh = conn.execute(
                        "SELECT * FROM reminder_occurrences WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if fresh is not None:
                        claimed.append(_occurrence_from_row(fresh))
        return claimed

    def update_occurrence(
        self,
        occurrence_id: str,
        update: DeliveryUpdate,
        *,
        now: datetime,
    ) -> ReminderOccurrence:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise TodoError("not_found", f"Occurrence 不存在: {occurrence_id}")
            current = _occurrence_from_row(row)
            if current.invalidated_at is not None:
                return current
            response_status = update.agent_response_status or current.agent_response_status
            conn.execute(
                """
                UPDATE reminder_occurrences SET state = ?, destination_kind = ?,
                    destination_id = ?, delivery_receipt = ?, agent_response_status = ?,
                    next_attempt_at = ?, claim_until = NULL, updated_at = ? WHERE id = ?
                """,
                (
                    update.state.value,
                    update.destination_kind,
                    update.destination_id,
                    update.delivery_receipt,
                    response_status.value,
                    _dump_dt(update.next_attempt_at),
                    _dump_dt(now),
                    occurrence_id,
                ),
            )
        return self.get_occurrence(occurrence_id)

    def update_agent_response_status(
        self,
        occurrence_id: str,
        status: AgentResponseStatus,
        *,
        now: datetime,
    ) -> ReminderOccurrence:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE reminder_occurrences SET agent_response_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, _dump_dt(now), occurrence_id),
            )
            if cursor.rowcount != 1:
                raise TodoError("not_found", f"Occurrence 不存在: {occurrence_id}")
        return self.get_occurrence(occurrence_id)

    def freeze_occurrence_context(
        self,
        occurrence_id: str,
        *,
        persistence_session_id: str,
        persistence_destination_id: str | None,
        persistence_reason: str,
        notification_mode: ReminderNotificationMode,
        notification_decision_kind: str,
        now: datetime,
    ) -> ReminderOccurrence:
        """Persist the first routing decision and never overwrite it on retries."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT persistence_session_id FROM reminder_occurrences WHERE id = ?",
                (occurrence_id,),
            ).fetchone()
            if row is None:
                raise TodoError("not_found", f"Occurrence 不存在: {occurrence_id}")
            if row["persistence_session_id"] is None:
                conn.execute(
                    """
                    UPDATE reminder_occurrences
                    SET persistence_session_id = ?, persistence_destination_id = ?,
                        persistence_reason = ?, notification_mode = ?,
                        notification_decision_kind = ?, updated_at = ?
                    WHERE id = ? AND persistence_session_id IS NULL
                    """,
                    (
                        persistence_session_id,
                        persistence_destination_id,
                        persistence_reason,
                        notification_mode.value,
                        notification_decision_kind,
                        _dump_dt(now),
                        occurrence_id,
                    ),
                )
        return self.get_occurrence(occurrence_id)

    def ensure_delivery(
        self,
        occurrence_id: str,
        projection_kind: ReminderProjectionKind,
        *,
        destination_id: str | None,
        now: datetime,
    ) -> ReminderDelivery:
        with self._transaction() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM reminder_occurrences WHERE id = ?", (occurrence_id,)
                ).fetchone()
                is None
            ):
                raise TodoError("not_found", f"Occurrence 不存在: {occurrence_id}")
            conn.execute(
                """
                INSERT OR IGNORE INTO reminder_occurrence_deliveries (
                    occurrence_id, projection_kind, destination_id, state, receipt,
                    error_kind, attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', NULL, NULL, 0, ?, ?)
                """,
                (
                    occurrence_id,
                    projection_kind.value,
                    destination_id,
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
        return self.get_delivery(occurrence_id, projection_kind)

    def update_delivery(
        self,
        occurrence_id: str,
        projection_kind: ReminderProjectionKind,
        *,
        state: ReminderDeliveryState,
        receipt: str | None = None,
        error_kind: str | None = None,
        now: datetime,
        increment_attempt: bool = True,
    ) -> ReminderDelivery:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE reminder_occurrence_deliveries
                SET state = ?, receipt = ?, error_kind = ?,
                    attempt_count = attempt_count + ?, updated_at = ?
                WHERE occurrence_id = ? AND projection_kind = ?
                """,
                (
                    state.value,
                    receipt,
                    error_kind,
                    1 if increment_attempt else 0,
                    _dump_dt(now),
                    occurrence_id,
                    projection_kind.value,
                ),
            )
            if cursor.rowcount != 1:
                raise TodoError(
                    "not_found",
                    f"Reminder delivery 不存在: {occurrence_id}/{projection_kind.value}",
                )
        return self.get_delivery(occurrence_id, projection_kind)

    def begin_delivery_attempt(
        self,
        occurrence_id: str,
        projection_kind: ReminderProjectionKind,
        *,
        now: datetime,
    ) -> tuple[ReminderDelivery, bool]:
        """Claim one at-most-once external send and persist uncertainty first."""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE reminder_occurrence_deliveries
                SET state = 'unknown', receipt = NULL, error_kind = 'send_in_progress',
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE occurrence_id = ? AND projection_kind = ? AND state = 'pending'
                """,
                (_dump_dt(now), occurrence_id, projection_kind.value),
            )
        return self.get_delivery(occurrence_id, projection_kind), cursor.rowcount == 1

    def get_delivery(
        self,
        occurrence_id: str,
        projection_kind: ReminderProjectionKind,
    ) -> ReminderDelivery:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM reminder_occurrence_deliveries
                WHERE occurrence_id = ? AND projection_kind = ?
                """,
                (occurrence_id, projection_kind.value),
            ).fetchone()
        if row is None:
            raise TodoError(
                "not_found",
                f"Reminder delivery 不存在: {occurrence_id}/{projection_kind.value}",
            )
        return _delivery_from_row(row)

    def list_deliveries(self, occurrence_id: str) -> tuple[ReminderDelivery, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminder_occurrence_deliveries
                WHERE occurrence_id = ? ORDER BY projection_kind
                """,
                (occurrence_id,),
            ).fetchall()
        return tuple(_delivery_from_row(row) for row in rows)

    def get_occurrence(self, occurrence_id: str) -> ReminderOccurrence:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone()
        if row is None:
            raise TodoError("not_found", f"Occurrence 不存在: {occurrence_id}")
        return _occurrence_from_row(row)

    def occurrence_is_deliverable(self, occurrence_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM reminder_occurrences o
                JOIN todos t ON t.id = o.todo_id
                WHERE o.id = ? AND o.state = 'claimed' AND o.invalidated_at IS NULL
                    AND t.status = 'open'
                """,
                (occurrence_id,),
            ).fetchone()
        return row is not None

    def next_wakeup_at(self) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(value) AS wake_at FROM (
                    SELECT next_fire_at_utc AS value FROM reminders
                    WHERE status = 'active' AND next_fire_at_utc IS NOT NULL
                    UNION ALL
                    SELECT COALESCE(next_attempt_at, created_at) AS value
                    FROM reminder_occurrences WHERE state IN ('pending', 'failed')
                        AND invalidated_at IS NULL
                )
                """
            ).fetchone()
        return _load_dt(row["wake_at"]) if row and row["wake_at"] else None

    def diagnostics(self, *, limit: int = 50) -> list[ReminderOccurrence]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminder_occurrences ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_occurrence_from_row(row) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('open', 'completed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    revision INTEGER NOT NULL,
                    origin_kind TEXT,
                    origin_id TEXT
                );

                CREATE TABLE IF NOT EXISTS todo_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    todo_id TEXT NOT NULL REFERENCES todos(id),
                    action TEXT NOT NULL,
                    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user', 'agent', 'system', 'unknown')),
                    session_id TEXT,
                    occurred_at TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_todo_history ON todo_history(todo_id, id DESC);

                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    todo_id TEXT NOT NULL REFERENCES todos(id),
                    kind TEXT NOT NULL CHECK (kind IN ('once', 'recurring')),
                    timezone TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    next_fire_at_utc TEXT,
                    status TEXT NOT NULL CHECK (status IN ('active', 'exhausted', 'disabled')),
                    fire_count INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    origin_session_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(status, next_fire_at_utc);
                CREATE INDEX IF NOT EXISTS idx_reminders_todo ON reminders(todo_id);

                CREATE TABLE IF NOT EXISTS reminder_occurrences (
                    id TEXT PRIMARY KEY,
                    reminder_id TEXT NOT NULL REFERENCES reminders(id),
                    todo_id TEXT NOT NULL REFERENCES todos(id),
                    scheduled_at_utc TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending', 'claimed', 'delivered', 'delivery_unknown', 'failed')
                    ),
                    destination_kind TEXT,
                    destination_id TEXT,
                    delivery_receipt TEXT,
                    agent_response_status TEXT NOT NULL CHECK (
                        agent_response_status IN ('pending', 'running', 'completed', 'failed', 'skipped')
                    ),
                    attempt_count INTEGER NOT NULL,
                    next_attempt_at TEXT,
                    claim_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    persistence_session_id TEXT,
                    persistence_destination_id TEXT,
                    persistence_reason TEXT,
                    notification_mode TEXT CHECK (
                        notification_mode IS NULL OR notification_mode IN ('smart', 'all')
                    ),
                    notification_decision_kind TEXT,
                    UNIQUE(reminder_id, scheduled_at_utc)
                );
                CREATE INDEX IF NOT EXISTS idx_occurrences_pending
                    ON reminder_occurrences(state, next_attempt_at, scheduled_at_utc);

                CREATE TABLE IF NOT EXISTS reminder_occurrence_deliveries (
                    occurrence_id TEXT NOT NULL REFERENCES reminder_occurrences(id),
                    projection_kind TEXT NOT NULL CHECK (
                        projection_kind IN ('im_fixed', 'im_agent', 'pet_fixed')
                    ),
                    destination_id TEXT,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending', 'delivered', 'failed', 'unknown', 'skipped')
                    ),
                    receipt TEXT,
                    error_kind TEXT,
                    attempt_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (occurrence_id, projection_kind)
                );
                """
            )
            reminder_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(reminders)").fetchall()
            }
            if "origin_session_id" not in reminder_columns:
                conn.execute("ALTER TABLE reminders ADD COLUMN origin_session_id TEXT")
                conn.execute(
                    """
                    UPDATE reminders
                    SET origin_session_id = (
                        SELECT origin_id FROM todos WHERE todos.id = reminders.todo_id
                            AND todos.origin_kind = 'session'
                    )
                    WHERE origin_session_id IS NULL
                    """
                )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(reminder_occurrences)").fetchall()
            }
            if "invalidated_at" not in columns:
                conn.execute("ALTER TABLE reminder_occurrences ADD COLUMN invalidated_at TEXT")
            if "invalidation_reason" not in columns:
                conn.execute("ALTER TABLE reminder_occurrences ADD COLUMN invalidation_reason TEXT")
            if "persistence_session_id" not in columns:
                conn.execute(
                    "ALTER TABLE reminder_occurrences ADD COLUMN persistence_session_id TEXT"
                )
            if "persistence_destination_id" not in columns:
                conn.execute(
                    "ALTER TABLE reminder_occurrences ADD COLUMN persistence_destination_id TEXT"
                )
            if "persistence_reason" not in columns:
                conn.execute("ALTER TABLE reminder_occurrences ADD COLUMN persistence_reason TEXT")
            if "notification_mode" not in columns:
                conn.execute("ALTER TABLE reminder_occurrences ADD COLUMN notification_mode TEXT")
            if "notification_decision_kind" not in columns:
                conn.execute(
                    "ALTER TABLE reminder_occurrences ADD COLUMN notification_decision_kind TEXT"
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
    def _transaction(
        self,
        *,
        todo_id: str | None = None,
        action: str = "",
        actor: TodoActor = UNKNOWN_ACTOR,
        now: datetime | None = None,
    ) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                before = snapshot(conn, todo_id) if todo_id is not None else {}
                yield conn
                if todo_id is not None and now is not None:
                    append_change(
                        conn,
                        todo_id=todo_id,
                        action=action,
                        actor=actor,
                        occurred_at=_dump_dt(now) or "",
                        before=before,
                        after=snapshot(conn, todo_id),
                    )
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    @staticmethod
    def _require_todo(conn: sqlite3.Connection, todo_id: str) -> Todo:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if row is None:
            raise TodoError("not_found", f"Todo 不存在: {todo_id}")
        return _todo_from_row(row)

    @staticmethod
    def _require_reminder(conn: sqlite3.Connection, reminder_id: str) -> Reminder:
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if row is None:
            raise TodoError("not_found", f"Reminder 不存在: {reminder_id}")
        return _reminder_from_row(row)

    @staticmethod
    def _require_revision(todo: Todo, revision: int) -> None:
        if todo.revision != revision:
            raise _revision_error(revision, todo.revision)

    @staticmethod
    def _invalidate_occurrences(
        conn: sqlite3.Connection,
        *,
        now: datetime,
        reason: str,
        todo_id: str | None = None,
        reminder_id: str | None = None,
    ) -> None:
        if (todo_id is None) == (reminder_id is None):
            raise ValueError("exactly one occurrence invalidation owner is required")
        column = "todo_id" if todo_id is not None else "reminder_id"
        owner_id = todo_id if todo_id is not None else reminder_id
        conn.execute(
            f"""
            UPDATE reminder_occurrences
            SET invalidated_at = ?, invalidation_reason = ?, claim_until = NULL,
                next_attempt_at = NULL, agent_response_status = 'skipped', updated_at = ?
            WHERE {column} = ? AND state IN ('pending', 'claimed', 'failed')
                AND invalidated_at IS NULL
            """,
            (_dump_dt(now), reason, _dump_dt(now), owner_id),
        )


def _todo_from_row(row: sqlite3.Row) -> Todo:
    return Todo(
        id=str(row["id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        status=TodoStatus(str(row["status"])),
        created_at=_required_dt(row["created_at"]),
        updated_at=_required_dt(row["updated_at"]),
        completed_at=_load_dt(row["completed_at"]),
        cancelled_at=_load_dt(row["cancelled_at"]),
        revision=int(row["revision"]),
        origin_kind=row["origin_kind"],
        origin_id=row["origin_id"],
    )


def _reminder_from_row(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=str(row["id"]),
        todo_id=str(row["todo_id"]),
        kind=ReminderKind(str(row["kind"])),
        timezone=str(row["timezone"]),
        schedule=schedule_from_dict(json.loads(str(row["schedule_json"]))),
        next_fire_at_utc=_load_dt(row["next_fire_at_utc"]),
        status=ReminderStatus(str(row["status"])),
        fire_count=int(row["fire_count"]),
        revision=int(row["revision"]),
        created_at=_required_dt(row["created_at"]),
        updated_at=_required_dt(row["updated_at"]),
        origin_session_id=(
            str(row["origin_session_id"]) if row["origin_session_id"] is not None else None
        ),
    )


def _occurrence_from_row(row: sqlite3.Row) -> ReminderOccurrence:
    raw_facts = json.loads(str(row["facts_json"]))
    facts = ReminderFacts(
        occurrence_id=str(raw_facts["occurrence_id"]),
        todo_id=str(raw_facts["todo_id"]),
        reminder_id=str(raw_facts["reminder_id"]),
        title=str(raw_facts["title"]),
        description=str(raw_facts["description"]),
        scheduled_at_utc=_required_dt(raw_facts["scheduled_at_utc"]),
        scheduled_local=datetime.fromisoformat(str(raw_facts["scheduled_local"])),
        timezone=str(raw_facts["timezone"]),
        recurrence=str(raw_facts["recurrence"]),
        todo_status=TodoStatus(str(raw_facts["todo_status"])),
    )
    return ReminderOccurrence(
        id=str(row["id"]),
        reminder_id=str(row["reminder_id"]),
        todo_id=str(row["todo_id"]),
        scheduled_at_utc=_required_dt(row["scheduled_at_utc"]),
        facts=facts,
        state=OccurrenceState(str(row["state"])),
        destination_kind=row["destination_kind"],
        destination_id=row["destination_id"],
        delivery_receipt=row["delivery_receipt"],
        agent_response_status=AgentResponseStatus(str(row["agent_response_status"])),
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=_load_dt(row["next_attempt_at"]),
        claim_until=_load_dt(row["claim_until"]),
        created_at=_required_dt(row["created_at"]),
        updated_at=_required_dt(row["updated_at"]),
        invalidated_at=_load_dt(row["invalidated_at"]),
        invalidation_reason=(
            str(row["invalidation_reason"]) if row["invalidation_reason"] is not None else None
        ),
        persistence_session_id=(
            str(row["persistence_session_id"])
            if row["persistence_session_id"] is not None
            else None
        ),
        persistence_destination_id=(
            str(row["persistence_destination_id"])
            if row["persistence_destination_id"] is not None
            else None
        ),
        persistence_reason=(
            str(row["persistence_reason"]) if row["persistence_reason"] is not None else None
        ),
        notification_mode=(
            ReminderNotificationMode(str(row["notification_mode"]))
            if row["notification_mode"] is not None
            else None
        ),
        notification_decision_kind=(
            str(row["notification_decision_kind"])
            if row["notification_decision_kind"] is not None
            else None
        ),
    )


def _delivery_from_row(row: sqlite3.Row) -> ReminderDelivery:
    return ReminderDelivery(
        occurrence_id=str(row["occurrence_id"]),
        projection_kind=ReminderProjectionKind(str(row["projection_kind"])),
        destination_id=(str(row["destination_id"]) if row["destination_id"] is not None else None),
        state=ReminderDeliveryState(str(row["state"])),
        receipt=str(row["receipt"]) if row["receipt"] is not None else None,
        error_kind=str(row["error_kind"]) if row["error_kind"] is not None else None,
        attempt_count=int(row["attempt_count"]),
        created_at=_required_dt(row["created_at"]),
        updated_at=_required_dt(row["updated_at"]),
    )


def _facts_to_dict(facts: ReminderFacts) -> dict[str, Any]:
    return {
        "occurrence_id": facts.occurrence_id,
        "todo_id": facts.todo_id,
        "reminder_id": facts.reminder_id,
        "title": facts.title,
        "description": facts.description,
        "scheduled_at_utc": _dump_dt(facts.scheduled_at_utc),
        "scheduled_local": facts.scheduled_local.isoformat(),
        "timezone": facts.timezone,
        "recurrence": facts.recurrence,
        "todo_status": facts.todo_status.value,
    }


def _dump_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _load_dt(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _required_dt(value: object) -> datetime:
    result = _load_dt(value)
    if result is None:
        raise ValueError("required datetime is null")
    return result


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TodoError("invalid_datetime", "日期时间必须包含时区")
    return value.astimezone(UTC)


def _revision_error(expected: int, actual: int) -> TodoError:
    return TodoError(
        "revision_conflict",
        "资源已被其他操作修改，请读取最新状态后重试",
        {"expected_revision": expected, "actual_revision": actual},
    )
