"""Backend-only REST boundary for Scheduled Agent Tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from agent.scheduled_tasks import (
    ScheduledOccurrence,
    ScheduledTaskError,
    ScheduledTaskListQuery,
    ScheduledTaskService,
    ScheduledTaskStatus,
)
from agent.scheduled_tasks.codec import occurrence_to_dict, schedule_from_input, task_to_dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..assembly import BridgeRuntime


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreateRequest(_Request):
    name: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=20_000)
    schedule: dict[str, Any]
    skill_id: str | None = None


class TaskPatchRequest(_Request):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instruction: str | None = Field(default=None, min_length=1, max_length=20_000)
    schedule: dict[str, Any] | None = None
    skill_id: str | None = None
    clear_skill: bool = False


class RevisionRequest(_Request):
    revision: int = Field(ge=1)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    def service() -> ScheduledTaskService:
        value = runtime.scheduled_task_service
        if value is None:
            raise HTTPException(status_code=503, detail="Scheduled Task service 未装配")
        return value

    @app.get("/v1/scheduled-tasks", tags=["scheduled-tasks"])
    def list_tasks(
        statuses: Annotated[list[ScheduledTaskStatus] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> object:
        try:
            effective_statuses = statuses or [
                ScheduledTaskStatus.ACTIVE,
                ScheduledTaskStatus.PAUSED,
                ScheduledTaskStatus.EXHAUSTED,
            ]
            items = service().list(
                ScheduledTaskListQuery(
                    statuses=tuple(effective_statuses), limit=limit, offset=offset
                )
            )
            return {"tasks": [task_to_dict(item) for item in items]}
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.post("/v1/scheduled-tasks", tags=["scheduled-tasks"])
    def create_task(payload: TaskCreateRequest) -> object:
        try:
            _validate_skill(runtime, payload.skill_id)
            persona = runtime.catalog.find_by_name(runtime.default_persona)
            task = service().create(
                name=payload.name,
                instruction=payload.instruction,
                schedule=schedule_from_input(payload.schedule, now=datetime.now(UTC)),
                persona_id=persona.id,
                persona_name=persona.name,
                model_id=runtime.default_model,
                skill_id=payload.skill_id,
            )
            return task_to_dict(task)
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.get("/v1/scheduled-tasks/{task_id}", tags=["scheduled-tasks"])
    def get_task(task_id: str) -> object:
        try:
            return task_to_dict(service().get(task_id))
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.patch("/v1/scheduled-tasks/{task_id}", tags=["scheduled-tasks"])
    def patch_task(task_id: str, payload: TaskPatchRequest) -> object:
        try:
            _validate_skill(runtime, payload.skill_id)
            task = service().update(
                task_id,
                revision=payload.revision,
                name=payload.name,
                instruction=payload.instruction,
                schedule=(
                    schedule_from_input(payload.schedule, now=datetime.now(UTC))
                    if payload.schedule is not None
                    else None
                ),
                skill_id=payload.skill_id,
                clear_skill=payload.clear_skill,
            )
            return task_to_dict(task)
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.post("/v1/scheduled-tasks/{task_id}/pause", tags=["scheduled-tasks"])
    def pause_task(task_id: str, payload: RevisionRequest) -> object:
        try:
            return task_to_dict(service().pause(task_id, revision=payload.revision))
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.post("/v1/scheduled-tasks/{task_id}/resume", tags=["scheduled-tasks"])
    def resume_task(task_id: str, payload: RevisionRequest) -> object:
        try:
            return task_to_dict(service().resume(task_id, revision=payload.revision))
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.delete("/v1/scheduled-tasks/{task_id}", tags=["scheduled-tasks"])
    def delete_task(task_id: str, revision: Annotated[int, Query(ge=1)]) -> object:
        try:
            return task_to_dict(service().delete(task_id, revision=revision))
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.post("/v1/scheduled-tasks/{task_id}/runs", tags=["scheduled-tasks"])
    def run_task_now(task_id: str) -> object:
        try:
            return _occurrence_payload(runtime, service().run_now(task_id))
        except ScheduledTaskError as exc:
            return _error(exc)

    @app.get("/v1/scheduled-tasks/{task_id}/occurrences", tags=["scheduled-tasks"])
    def task_occurrences(
        task_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> object:
        try:
            return {
                "occurrences": [
                    _occurrence_payload(runtime, item)
                    for item in service().history(task_id, limit=limit, offset=offset)
                ]
            }
        except ScheduledTaskError as exc:
            return _error(exc)


def _validate_skill(runtime: BridgeRuntime, skill_id: str | None) -> None:
    if skill_id is None:
        return
    service = runtime.skill_service
    if service is None or skill_id not in service.snapshot.by_id():
        raise ScheduledTaskError(
            "skill_not_found",
            "skill_id 不在当前可信 Skill catalog 中",
        )


def _occurrence_payload(
    runtime: BridgeRuntime,
    occurrence: ScheduledOccurrence,
) -> dict[str, Any]:
    waiting = False
    broker = runtime.filesystem_approval_broker
    if broker is not None and occurrence.session_id is not None and occurrence.run_id is not None:
        waiting = bool(
            broker.pending(
                session_id=occurrence.session_id,
                run_id=occurrence.run_id,
            )
        )
    return occurrence_to_dict(occurrence, waiting_for_approval=waiting)


def _error(exc: ScheduledTaskError) -> JSONResponse:
    status = (
        404
        if exc.kind in {"task_not_found", "occurrence_not_found"}
        else 409
        if exc.kind
        in {
            "revision_conflict",
            "invalid_task_state",
            "invalid_occurrence_state",
        }
        else 400
    )
    return JSONResponse(
        status_code=status,
        content={"error_kind": exc.kind, "message": exc.message, **exc.details},
    )
