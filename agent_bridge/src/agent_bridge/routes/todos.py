"""070 · Todo 与 Reminder 的统一 REST 控制面。"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Literal

from agent.todos import (
    Reminder,
    TodoDetails,
    TodoError,
    TodoListQuery,
    TodoService,
    TodoStatus,
    schedule_from_dict,
    schedule_to_dict,
)
from agent.todos.history import TodoActor
from agent.todos.models import OnceSchedule
from agent.todos.recurrence import first_fire_at
from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..assembly import BridgeRuntime


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TodoCreateRequest(_Request):
    title: str
    description: str = ""
    reminders: list[dict[str, Any]] = Field(default_factory=list)


class TodoPatchRequest(_Request):
    revision: int = Field(ge=1)
    title: str | None = None
    description: str | None = None


class RevisionRequest(_Request):
    revision: int = Field(ge=1)


class ReminderCreateRequest(_Request):
    schedule: dict[str, Any]


class ReminderPatchRequest(_Request):
    revision: int = Field(ge=1)
    schedule: dict[str, Any]
    enabled: bool = True


class DesktopTodoRequest(_Request):
    action: Literal[
        "create",
        "update",
        "complete",
        "cancel",
        "reopen",
        "add_reminder",
        "update_reminder",
        "remove_reminder",
    ]
    todo_id: str | None = None
    reminder_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def register_routes(
    app: FastAPI, runtime: BridgeRuntime, *, owner_token: str | None = None
) -> None:
    router = APIRouter(prefix="/v1/todos", tags=["todos"])

    def service() -> TodoService:
        if runtime.todo_service is None:
            raise RuntimeError("Todo service is not configured")
        return runtime.todo_service

    @router.get("")
    def list_todos(
        status: str = "open",
        limit: int = 100,
        offset: int = 0,
        search: str = "",
    ) -> object:
        try:
            statuses = tuple(TodoStatus(item) for item in status.split(",") if item)
            return {
                "todos": [
                    _details(item)
                    for item in service().list(
                        TodoListQuery(statuses=statuses, limit=limit, offset=offset, search=search)
                    )
                ]
            }
        except (TodoError, ValueError) as exc:
            return _todo_error(exc)

    @router.post("")
    def create_todo(payload: TodoCreateRequest) -> object:
        try:
            result = service().create(
                title=payload.title,
                description=payload.description,
                reminders=tuple(schedule_from_dict(item) for item in payload.reminders),
                origin_kind="rest",
            )
            return _details(result)
        except TodoError as exc:
            return _todo_error(exc)

    @router.get("/{todo_id}")
    def get_todo(todo_id: str) -> object:
        try:
            return _details(service().get(todo_id))
        except TodoError as exc:
            return _todo_error(exc)

    @router.get("/{todo_id}/history")
    def history(todo_id: str, before_id: int | None = None, limit: int = 50) -> object:
        try:
            items = service().store.history(todo_id, before_id=before_id, limit=limit)
            return {"items": items, "nextCursor": items[-1]["id"] if len(items) == limit else None}
        except TodoError as exc:
            return _todo_error(exc)

    @router.patch("/{todo_id}")
    def patch_todo(todo_id: str, payload: TodoPatchRequest) -> object:
        try:
            return _details(
                service().update(
                    todo_id,
                    revision=payload.revision,
                    title=payload.title,
                    description=payload.description,
                )
            )
        except TodoError as exc:
            return _todo_error(exc)

    @router.post("/{todo_id}/complete")
    def complete_todo(todo_id: str, payload: RevisionRequest) -> object:
        try:
            return _details(service().complete(todo_id, revision=payload.revision))
        except TodoError as exc:
            return _todo_error(exc)

    @router.post("/{todo_id}/cancel")
    def cancel_todo(todo_id: str, payload: RevisionRequest) -> object:
        try:
            return _details(service().cancel(todo_id, revision=payload.revision))
        except TodoError as exc:
            return _todo_error(exc)

    @router.post("/{todo_id}/reopen")
    def reopen_todo(todo_id: str, payload: RevisionRequest) -> object:
        try:
            return _details(service().reopen(todo_id, revision=payload.revision))
        except TodoError as exc:
            return _todo_error(exc)

    @router.post("/{todo_id}/reminders")
    def create_reminder(todo_id: str, payload: ReminderCreateRequest) -> object:
        try:
            return _reminder(
                service().add_reminder(todo_id, schedule=schedule_from_dict(payload.schedule))
            )
        except TodoError as exc:
            return _todo_error(exc)

    @router.patch("/{todo_id}/reminders/{reminder_id}")
    def patch_reminder(
        todo_id: str,
        reminder_id: str,
        payload: ReminderPatchRequest,
    ) -> object:
        try:
            return _reminder(
                service().update_reminder(
                    todo_id,
                    reminder_id,
                    revision=payload.revision,
                    schedule=schedule_from_dict(payload.schedule),
                    enabled=payload.enabled,
                )
            )
        except TodoError as exc:
            return _todo_error(exc)

    @router.delete("/{todo_id}/reminders/{reminder_id}")
    def remove_reminder(todo_id: str, reminder_id: str, revision: int) -> object:
        try:
            return _reminder(service().remove_reminder(todo_id, reminder_id, revision=revision))
        except TodoError as exc:
            return _todo_error(exc)

    app.include_router(router)

    @app.post("/internal/todos/mutate", include_in_schema=False)
    def desktop_mutate(
        request: DesktopTodoRequest,
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> object:
        if (
            not owner_token
            or not x_agent_friend_lifecycle_token
            or not secrets.compare_digest(
                owner_token,
                x_agent_friend_lifecycle_token,
            )
        ):
            raise HTTPException(status_code=401, detail="invalid desktop authorization")
        actor = TodoActor("user")
        try:
            if request.action == "create":
                create = TodoCreateRequest.model_validate(request.payload)
                return _details(
                    service().create(
                        title=create.title,
                        description=create.description,
                        reminders=tuple(schedule_from_dict(item) for item in create.reminders),
                        origin_kind="desktop",
                        actor=actor,
                    )
                )
            if not request.todo_id:
                raise TodoError("invalid_arguments", "缺少待办标识")
            if request.action == "update":
                patch = TodoPatchRequest.model_validate(request.payload)
                return _details(
                    service().update(
                        request.todo_id,
                        revision=patch.revision,
                        title=patch.title,
                        description=patch.description,
                        actor=actor,
                    )
                )
            if request.action in {"complete", "cancel", "reopen"}:
                revision = RevisionRequest.model_validate(request.payload).revision
                transition = getattr(service(), request.action)
                return _details(transition(request.todo_id, revision=revision, actor=actor))
            if request.action == "add_reminder":
                create_reminder = ReminderCreateRequest.model_validate(request.payload)
                return _reminder(
                    service().add_reminder(
                        request.todo_id,
                        schedule=schedule_from_dict(create_reminder.schedule),
                        actor=actor,
                    )
                )
            if not request.reminder_id:
                raise TodoError("invalid_arguments", "缺少提醒标识")
            if request.action == "update_reminder":
                patch_reminder = ReminderPatchRequest.model_validate(request.payload)
                return _reminder(
                    service().update_reminder(
                        request.todo_id,
                        request.reminder_id,
                        revision=patch_reminder.revision,
                        schedule=schedule_from_dict(patch_reminder.schedule),
                        enabled=patch_reminder.enabled,
                        actor=actor,
                    )
                )
            revision = RevisionRequest.model_validate(request.payload).revision
            return _reminder(
                service().remove_reminder(
                    request.todo_id,
                    request.reminder_id,
                    revision=revision,
                    actor=actor,
                )
            )
        except (TodoError, ValueError, ValidationError) as exc:
            return _todo_error(exc)


def _todo_error(exc: TodoError | ValueError) -> JSONResponse:
    if isinstance(exc, TodoError):
        code = exc.kind
        message = exc.message
        details = exc.details
    else:
        code = "invalid_arguments"
        message = str(exc)
        details = {}
    status = 404 if code == "not_found" else 409 if code == "revision_conflict" else 422
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _details(value: TodoDetails) -> dict[str, Any]:
    todo = value.todo
    return {
        "id": todo.id,
        "title": todo.title,
        "description": todo.description,
        "status": todo.status.value,
        "revision": todo.revision,
        "lastOperation": value.last_operation,
        "createdAt": _dt(todo.created_at),
        "updatedAt": _dt(todo.updated_at),
        "completedAt": _dt(todo.completed_at),
        "cancelledAt": _dt(todo.cancelled_at),
        "reminders": [
            _reminder(item, value.last_occurrences.get(item.id)) for item in value.reminders
        ],
    }


def _reminder(value: Reminder, last_occurrence: datetime | None = None) -> dict[str, Any]:
    return {
        "id": value.id,
        "todoId": value.todo_id,
        "kind": value.kind.value,
        "timezone": value.timezone,
        "schedule": schedule_to_dict(value.schedule),
        "nextFireAtUtc": _dt(value.next_fire_at_utc),
        "onceAtUtc": _dt(first_fire_at(value.schedule))
        if isinstance(value.schedule, OnceSchedule)
        else None,
        "lastOccurrenceAtUtc": _dt(last_occurrence),
        "status": value.status.value,
        "fireCount": value.fire_count,
        "revision": value.revision,
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


__all__ = ["register_routes"]
