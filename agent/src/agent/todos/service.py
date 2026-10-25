"""Todo 应用服务：所有入口共享的校验与生命周期边界。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from .history import UNKNOWN_ACTOR, TodoActor
from .models import (
    OnceSchedule,
    Reminder,
    ReminderKind,
    ReminderSchedule,
    ReminderStatus,
    Todo,
    TodoDetails,
    TodoError,
    TodoListQuery,
    TodoStatus,
)
from .recurrence import first_fire_at, validate_schedule
from .store import TodoStore

Clock = Callable[[], datetime]


class TodoService:
    def __init__(self, store: TodoStore, *, clock: Clock | None = None) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wake_listeners: list[Callable[[], None]] = []

    def add_wake_listener(self, listener: Callable[[], None]) -> None:
        self._wake_listeners.append(listener)

    def create(
        self,
        *,
        title: str,
        description: str = "",
        reminders: tuple[ReminderSchedule, ...] = (),
        origin_kind: str | None = None,
        origin_id: str | None = None,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> TodoDetails:
        title = _title(title)
        description = _description(description)
        now = self._now()
        todo = Todo(
            id=str(uuid.uuid4()),
            title=title,
            description=description,
            status=TodoStatus.OPEN,
            created_at=now,
            updated_at=now,
            origin_kind=origin_kind,
            origin_id=origin_id,
        )
        prepared = tuple(
            self._prepare_reminder(
                todo.id,
                schedule=schedule,
                origin_session_id=origin_id if origin_kind == "session" else None,
            )
            for schedule in reminders
        )
        self.store.create_todo(todo, reminders=prepared, actor=actor)
        self._notify_wake()
        return self.store.get_todo(todo.id)

    def get(self, todo_id: str) -> TodoDetails:
        return self.store.get_todo(todo_id)

    def list(self, query: TodoListQuery | None = None) -> list[TodoDetails]:
        return self.store.list_todos(query or TodoListQuery())

    def update(
        self,
        todo_id: str,
        *,
        revision: int,
        actor: TodoActor = UNKNOWN_ACTOR,
        title: str | None = None,
        description: str | None = None,
    ) -> TodoDetails:
        current = self.store.get_todo(todo_id).todo
        resolved_title = current.title if title is None else _title(title)
        resolved_description = (
            current.description if description is None else _description(description)
        )
        self.store.update_todo_fields(
            todo_id,
            revision=revision,
            title=resolved_title,
            description=resolved_description,
            now=self._now(),
            actor=actor,
        )
        return self.store.get_todo(todo_id)

    def complete(
        self, todo_id: str, *, revision: int, actor: TodoActor = UNKNOWN_ACTOR
    ) -> TodoDetails:
        self.store.set_todo_status(
            todo_id, revision=revision, status=TodoStatus.COMPLETED, now=self._now(), actor=actor
        )
        self._notify_wake()
        return self.store.get_todo(todo_id)

    def cancel(
        self, todo_id: str, *, revision: int, actor: TodoActor = UNKNOWN_ACTOR
    ) -> TodoDetails:
        self.store.set_todo_status(
            todo_id, revision=revision, status=TodoStatus.CANCELLED, now=self._now(), actor=actor
        )
        self._notify_wake()
        return self.store.get_todo(todo_id)

    def reopen(
        self, todo_id: str, *, revision: int, actor: TodoActor = UNKNOWN_ACTOR
    ) -> TodoDetails:
        self.store.set_todo_status(
            todo_id, revision=revision, status=TodoStatus.OPEN, now=self._now(), actor=actor
        )
        return self.store.get_todo(todo_id)

    def add_reminder(
        self,
        todo_id: str,
        *,
        schedule: ReminderSchedule,
        origin_session_id: str | None = None,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Reminder:
        reminder = self._prepare_reminder(
            todo_id, schedule=schedule, origin_session_id=origin_session_id
        )
        result = self.store.create_reminder(reminder, actor=actor)
        self._notify_wake()
        return result

    def _prepare_reminder(
        self,
        todo_id: str,
        *,
        schedule: ReminderSchedule,
        origin_session_id: str | None = None,
    ) -> Reminder:
        validate_schedule(schedule)
        now = self._now()
        next_fire = first_fire_at(schedule)
        if isinstance(schedule, OnceSchedule) and next_fire is not None and next_fire <= now:
            raise TodoError("invalid_datetime", "一次性 Reminder 必须指向未来时间")
        reminder = Reminder(
            id=str(uuid.uuid4()),
            todo_id=todo_id,
            kind=(
                ReminderKind.ONCE if isinstance(schedule, OnceSchedule) else ReminderKind.RECURRING
            ),
            timezone=schedule.timezone,
            schedule=schedule,
            next_fire_at_utc=next_fire,
            status=ReminderStatus.ACTIVE,
            fire_count=0,
            revision=1,
            created_at=now,
            updated_at=now,
            origin_session_id=origin_session_id,
        )
        return reminder

    def update_reminder(
        self,
        todo_id: str,
        reminder_id: str,
        *,
        revision: int,
        actor: TodoActor = UNKNOWN_ACTOR,
        schedule: ReminderSchedule,
        enabled: bool = True,
    ) -> Reminder:
        current = self.store.get_reminder(reminder_id)
        if current.todo_id != todo_id:
            raise TodoError("not_found", f"Reminder 不属于 Todo: {todo_id}")
        validate_schedule(schedule)
        now = self._now()
        next_fire = first_fire_at(schedule) if enabled else None
        if (
            enabled
            and isinstance(schedule, OnceSchedule)
            and next_fire is not None
            and next_fire <= now
        ):
            raise TodoError("invalid_datetime", "一次性 Reminder 必须指向未来时间")
        updated = replace(
            current,
            kind=(
                ReminderKind.ONCE if isinstance(schedule, OnceSchedule) else ReminderKind.RECURRING
            ),
            timezone=schedule.timezone,
            schedule=schedule,
            next_fire_at_utc=next_fire,
            status=ReminderStatus.ACTIVE if enabled else ReminderStatus.DISABLED,
            fire_count=0,
            revision=current.revision + 1,
            updated_at=now,
        )
        result = self.store.update_reminder(updated, expected_revision=revision, actor=actor)
        self._notify_wake()
        return result

    def remove_reminder(
        self,
        todo_id: str,
        reminder_id: str,
        *,
        revision: int,
        actor: TodoActor = UNKNOWN_ACTOR,
    ) -> Reminder:
        result = self.store.disable_reminder(
            todo_id, reminder_id, revision=revision, now=self._now(), actor=actor
        )
        self._notify_wake()
        return result

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise TodoError("invalid_datetime", "TodoService clock 必须返回带时区时间")
        return value.astimezone(UTC)

    def _notify_wake(self) -> None:
        for listener in tuple(self._wake_listeners):
            listener()


def _title(value: str) -> str:
    result = value.strip()
    if not result:
        raise TodoError("invalid_arguments", "Todo 标题不能为空")
    if len(result) > 500:
        raise TodoError("invalid_arguments", "Todo 标题不能超过 500 个字符")
    return result


def _description(value: str) -> str:
    result = value.strip()
    if len(result) > 20_000:
        raise TodoError("invalid_arguments", "Todo 说明不能超过 20000 个字符")
    return result
