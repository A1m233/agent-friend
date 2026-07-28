"""070 · Todo 与 Reminder 的统一 REST 控制面。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

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


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
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
    ) -> object:
        try:
            statuses = tuple(TodoStatus(item) for item in status.split(",") if item)
            return {
                "todos": [
                    _details(item)
                    for item in service().list(
                        TodoListQuery(statuses=statuses, limit=limit, offset=offset)
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
        "createdAt": _dt(todo.created_at),
        "updatedAt": _dt(todo.updated_at),
        "completedAt": _dt(todo.completed_at),
        "cancelledAt": _dt(todo.cancelled_at),
        "reminders": [_reminder(item) for item in value.reminders],
    }


def _reminder(value: Reminder) -> dict[str, Any]:
    return {
        "id": value.id,
        "todoId": value.todo_id,
        "kind": value.kind.value,
        "timezone": value.timezone,
        "schedule": schedule_to_dict(value.schedule),
        "nextFireAtUtc": _dt(value.next_fire_at_utc),
        "status": value.status.value,
        "fireCount": value.fire_count,
        "revision": value.revision,
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


__all__ = ["register_routes"]
