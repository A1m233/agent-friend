"""Application service shared by Agent tools, REST, and the schedule driver."""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from ..scheduling import (
    OnceSchedule,
    Schedule,
    ScheduleError,
    first_fire_at,
    next_fire_after,
    validate_schedule,
)
from .models import (
    ScheduledOccurrence,
    ScheduledTask,
    ScheduledTaskError,
    ScheduledTaskListQuery,
    ScheduledTaskStatus,
)
from .store import ScheduledTaskStore

Clock = Callable[[], datetime]


class ScheduledTaskService:
    def __init__(self, store: ScheduledTaskStore, *, clock: Clock | None = None) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wake_listeners: builtins.list[Callable[[], None]] = []

    def add_wake_listener(self, listener: Callable[[], None]) -> None:
        self._wake_listeners.append(listener)

    def create(
        self,
        *,
        name: str,
        instruction: str,
        schedule: Schedule,
        persona_name: str,
        model_id: str,
        persona_id: str | None = None,
        skill_id: str | None = None,
        origin_session_id: str | None = None,
    ) -> ScheduledTask:
        now = self._now()
        next_fire = _next_future(schedule, now, generated_count=0, once_must_be_future=True)
        status = (
            ScheduledTaskStatus.ACTIVE if next_fire is not None else ScheduledTaskStatus.EXHAUSTED
        )
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            name=_name(name),
            instruction=_instruction(instruction),
            schedule=schedule,
            next_fire_at_utc=next_fire,
            status=status,
            persona_id=_optional_text(persona_id),
            persona_name=_required_text(persona_name, "persona_name"),
            model_id=_required_text(model_id, "model_id"),
            skill_id=_optional_text(skill_id),
            origin_session_id=_optional_text(origin_session_id),
            fire_count=0,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        result = self.store.create_task(task)
        self._notify_wake()
        return result

    def get(self, task_id: str) -> ScheduledTask:
        return self.store.get_task(task_id)

    def list(self, query: ScheduledTaskListQuery | None = None) -> list[ScheduledTask]:
        return self.store.list_tasks(query or ScheduledTaskListQuery())

    def update(
        self,
        task_id: str,
        *,
        revision: int,
        name: str | None = None,
        instruction: str | None = None,
        schedule: Schedule | None = None,
        persona_id: str | None = None,
        persona_name: str | None = None,
        model_id: str | None = None,
        skill_id: str | None = None,
        clear_skill: bool = False,
    ) -> ScheduledTask:
        if (
            name is None
            and instruction is None
            and schedule is None
            and persona_id is None
            and persona_name is None
            and model_id is None
            and skill_id is None
            and not clear_skill
        ):
            raise ScheduledTaskError("invalid_arguments", "update 至少需要一个实际变更字段")
        if clear_skill and skill_id is not None:
            raise ScheduledTaskError(
                "invalid_arguments",
                "skill_id 与 clear_skill=true 不能同时提供",
            )
        current = self.store.get_task(task_id)
        if current.status is ScheduledTaskStatus.DELETED:
            raise ScheduledTaskError("invalid_task_state", "已删除任务不能修改")
        now = self._now()
        next_schedule = schedule or current.schedule
        next_fire = current.next_fire_at_utc
        next_status = current.status
        if schedule is not None and current.status is not ScheduledTaskStatus.PAUSED:
            next_fire = _next_future(schedule, now, generated_count=0, once_must_be_future=True)
            next_status = (
                ScheduledTaskStatus.ACTIVE
                if next_fire is not None
                else ScheduledTaskStatus.EXHAUSTED
            )
        updated = replace(
            current,
            name=current.name if name is None else _name(name),
            instruction=(current.instruction if instruction is None else _instruction(instruction)),
            schedule=next_schedule,
            next_fire_at_utc=next_fire,
            status=next_status,
            persona_id=current.persona_id if persona_id is None else _optional_text(persona_id),
            persona_name=(
                current.persona_name
                if persona_name is None
                else _required_text(persona_name, "persona_name")
            ),
            model_id=(
                current.model_id if model_id is None else _required_text(model_id, "model_id")
            ),
            skill_id=(
                None
                if clear_skill
                else current.skill_id
                if skill_id is None
                else _optional_text(skill_id)
            ),
            fire_count=0 if schedule is not None else current.fire_count,
            revision=current.revision + 1,
            updated_at=now,
        )
        result = self.store.update_task(updated, expected_revision=revision)
        self._notify_wake()
        return result

    def pause(self, task_id: str, *, revision: int) -> ScheduledTask:
        current = self.store.get_task(task_id)
        if current.status is not ScheduledTaskStatus.ACTIVE:
            raise ScheduledTaskError("invalid_task_state", "只有 active 任务可以暂停")
        result = self.store.update_task(
            replace(
                current,
                status=ScheduledTaskStatus.PAUSED,
                next_fire_at_utc=None,
                revision=current.revision + 1,
                updated_at=self._now(),
            ),
            expected_revision=revision,
        )
        self._notify_wake()
        return result

    def resume(self, task_id: str, *, revision: int) -> ScheduledTask:
        current = self.store.get_task(task_id)
        if current.status is not ScheduledTaskStatus.PAUSED:
            raise ScheduledTaskError("invalid_task_state", "只有 paused 任务可以恢复")
        now = self._now()
        next_fire = _next_future(
            current.schedule,
            now,
            generated_count=current.fire_count,
            once_must_be_future=False,
        )
        result = self.store.update_task(
            replace(
                current,
                status=(
                    ScheduledTaskStatus.ACTIVE
                    if next_fire is not None
                    else ScheduledTaskStatus.EXHAUSTED
                ),
                next_fire_at_utc=next_fire,
                revision=current.revision + 1,
                updated_at=now,
            ),
            expected_revision=revision,
        )
        self._notify_wake()
        return result

    def delete(self, task_id: str, *, revision: int) -> ScheduledTask:
        current = self.store.get_task(task_id)
        if current.revision != revision:
            raise ScheduledTaskError(
                "revision_conflict",
                f"Scheduled Task revision 冲突：期望 {revision}，实际 {current.revision}",
                {"expected_revision": revision, "actual_revision": current.revision},
            )
        if current.status is ScheduledTaskStatus.DELETED:
            return current
        now = self._now()
        result = self.store.update_task(
            replace(
                current,
                status=ScheduledTaskStatus.DELETED,
                next_fire_at_utc=None,
                revision=current.revision + 1,
                updated_at=now,
                deleted_at=now,
            ),
            expected_revision=revision,
        )
        self._notify_wake()
        return result

    def run_now(self, task_id: str) -> ScheduledOccurrence:
        result = self.store.create_manual_occurrence(task_id, now=self._now())
        self._notify_wake()
        return result

    def history(
        self,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[ScheduledOccurrence]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ScheduledTaskError(
                "invalid_arguments", "history limit 必须为 1-100，offset 不能小于 0"
            )
        return self.store.list_occurrences(task_id, limit=limit, offset=offset)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ScheduledTaskError("invalid_datetime", "ScheduledTaskService clock 必须带时区")
        return value.astimezone(UTC)

    def _notify_wake(self) -> None:
        for listener in tuple(self._wake_listeners):
            listener()


def _next_future(
    schedule: Schedule,
    now: datetime,
    *,
    generated_count: int,
    once_must_be_future: bool,
) -> datetime | None:
    try:
        validate_schedule(schedule)
        first = first_fire_at(schedule)
        if first is None:
            return None
        if first > now:
            return first
        if isinstance(schedule, OnceSchedule):
            if once_must_be_future:
                raise ScheduledTaskError("invalid_schedule", "一次性任务必须指向未来时间")
            return None
        return next_fire_after(schedule, now, generated_count=generated_count)
    except ScheduleError as exc:
        raise ScheduledTaskError(exc.kind, exc.message, exc.details or {}) from exc


def _name(value: str) -> str:
    result = value.strip()
    if not result:
        raise ScheduledTaskError("invalid_arguments", "任务名称不能为空")
    if len(result) > 200:
        raise ScheduledTaskError("invalid_arguments", "任务名称不能超过 200 个字符")
    return result


def _instruction(value: str) -> str:
    result = value.strip()
    if not result:
        raise ScheduledTaskError("invalid_arguments", "任务指令不能为空")
    if len(result) > 20_000:
        raise ScheduledTaskError("invalid_arguments", "任务指令不能超过 20000 个字符")
    return result


def _required_text(value: str, field: str) -> str:
    result = value.strip()
    if not result:
        raise ScheduledTaskError("invalid_arguments", f"{field} 不能为空")
    return result


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    result = value.strip()
    return result or None
