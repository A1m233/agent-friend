"""Backend-only REST boundary for Scheduled Agent Tasks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from agent.scheduled_tasks import (
    ScheduledOccurrence,
    ScheduledTaskError,
    ScheduledTaskListQuery,
    ScheduledTaskService,
    ScheduledTaskStatus,
)
from agent.scheduled_tasks.codec import occurrence_to_dict, schedule_from_input, task_to_dict
from agent.scheduling import ScheduleError, first_fire_at, next_fire_after
from agent.session_requests import RequestOptions
from agent.sessions import SessionNotFoundError
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from llm_providers import validate_model_runtime_config

from ..assembly import BridgeRuntime
from ..attachment_inputs import (
    DraftAttachmentInput,
    ExistingAttachmentInput,
    prepare_attachment_input,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreateRequest(_Request):
    notify_bound_im: StrictBool = False
    name: str = Field(min_length=1, max_length=200)
    instruction: str = Field(max_length=20_000)
    schedule: dict[str, Any]
    skill_id: str | None = None
    target_session_id: str | None = None
    request_options: dict[str, Any] | None = None
    draft_attachments: list[dict[str, str]] = Field(default_factory=list)


class TaskPatchRequest(_Request):
    notify_bound_im: StrictBool | None = None
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instruction: str | None = Field(default=None, max_length=20_000)
    schedule: dict[str, Any] | None = None
    skill_id: str | None = None
    target_session_id: str | None = None
    request_options: dict[str, Any] | None = None
    draft_attachments: list[dict[str, str]] = Field(default_factory=list)
    clear_skill: bool = False


class RevisionRequest(_Request):
    revision: int = Field(ge=1)


class SchedulePreviewRequest(_Request):
    schedule: dict[str, Any]


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    def service() -> ScheduledTaskService:
        value = runtime.scheduled_task_service
        if value is None:
            raise HTTPException(status_code=503, detail="Scheduled Task service 未装配")
        return value

    @app.post("/v1/scheduled-tasks/preview", tags=["scheduled-tasks"])
    def preview_schedule(payload: SchedulePreviewRequest) -> object:
        try:
            now = datetime.now(UTC)
            schedule = schedule_from_input(payload.schedule, now=now)
            first = first_fire_at(schedule)
            next_fire = (
                first
                if first is not None and first > now
                else next_fire_after(schedule, now, generated_count=0)
            )
            return {"next_fire_at_utc": next_fire.isoformat() if next_fire else None}
        except (ScheduledTaskError, ScheduleError) as exc:
            return _error(ScheduledTaskError(exc.kind, exc.message))

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
            with _task_inputs(runtime, payload) as options:
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
                    target_session_id=payload.target_session_id,
                    request_options=options,
                    notify_bound_im=payload.notify_bound_im,
                )
                return task_to_dict(task)
        except (ValueError, KeyError, TypeError) as exc:
            return _error(ScheduledTaskError("invalid_arguments", str(exc)))
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
            with _task_inputs(runtime, payload) as options:
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
                    target_session_id=payload.target_session_id,
                    request_options=options,
                    clear_skill=payload.clear_skill,
                    notify_bound_im=payload.notify_bound_im,
                )
                return task_to_dict(task)
        except (ValueError, KeyError, TypeError) as exc:
            return _error(ScheduledTaskError("invalid_arguments", str(exc)))
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


@contextmanager
def _task_inputs(
    runtime: BridgeRuntime, payload: TaskCreateRequest | TaskPatchRequest
) -> Iterator[RequestOptions | None]:
    if payload.request_options is None and not payload.draft_attachments:
        yield None
        return
    store = runtime.attachment_store
    with store.reference_guard if store else nullcontext():
        if payload.request_options is None and not payload.draft_attachments:
            yield None
            return
        options = RequestOptions.from_dict(payload.request_options or {})
        if options.model is not None:
            if runtime.connection_runtime is not None:
                runtime.connection_runtime.directory().validate(options.model)
            else:
                validate_model_runtime_config(options.model)
        plan = prepare_attachment_input(
            [
                *[ExistingAttachmentInput(ref) for ref in options.attachments],
                *[
                    DraftAttachmentInput(
                        draft_id=item["draft_id"], kind=_attachment_kind(item["kind"])
                    )
                    for item in payload.draft_attachments
                ],
            ],
            model_id=options.model.model_id
            if options.model
            else _target_model(runtime, payload.target_session_id),
            store=store,
            capability_resolver=runtime.attachment_capabilities,
            session_files_available=runtime.session_files_available,
        )
        yield replace(options, attachments=tuple(plan.resolve()))


def _target_model(runtime: BridgeRuntime, session_id: str | None) -> str:
    if session_id:
        try:
            return runtime.persistent_session_manager.query(session_id).current_model
        except SessionNotFoundError as exc:
            raise ScheduledTaskError(
                "target_session_missing", "目标会话已不存在，请选择其它会话。"
            ) from exc
    return runtime.default_model


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
    broker = runtime.approval_broker
    if broker is not None and occurrence.session_id is not None and occurrence.run_id is not None:
        waiting = bool(
            broker.pending(
                session_id=occurrence.session_id,
                run_id=occurrence.run_id,
            )
        )
    payload = occurrence_to_dict(occurrence, waiting_for_approval=waiting)
    if runtime.scheduled_task_service is not None:
        payload["im_notification"] = runtime.scheduled_task_service.store.notifications.summary(
            occurrence.id
        )
    receipt = (
        runtime.session_request_service.store.get(occurrence.run_id)
        if runtime.session_request_service and occurrence.run_id
        else None
    )
    payload["effective_options"] = receipt["effective_options"] if receipt else None
    if receipt and not receipt["input_accepted"]:
        payload["user_event_id"] = None
    return payload


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


def _attachment_kind(value: str) -> Literal["image", "document"]:
    if value == "image":
        return "image"
    if value == "document":
        return "document"
    raise ValueError("invalid attachment kind")
