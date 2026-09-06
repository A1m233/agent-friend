"""Local desktop notification snapshots and validated presentation receipts."""

from __future__ import annotations

from typing import Annotated, Any

from agent.notifications import NotificationConflict, NotificationNotFound
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..assembly import BridgeRuntime
from ..notifications import NotificationService
from ..notifications.debug import (
    DebugScenario,
    clear_debug_notifications,
    create_debug_notifications,
)


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    window_id: str = Field(alias="windowId", min_length=1, max_length=200)


class PresentedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=160)
    truncated: bool = False


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    silent_replacement_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, alias="silentReplacementIds", max_length=4
    )

    @field_validator("silent_replacement_ids")
    @classmethod
    def unique_replacement_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class DebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: DebugScenario


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/notifications", tags=["notifications"])

    def service() -> NotificationService:
        if runtime.notification_service is None:
            raise HTTPException(status_code=503, detail="notifications unavailable")
        return runtime.notification_service

    @router.get("")
    def snapshot() -> dict[str, Any]:
        return {"notifications": service().snapshot()}

    if runtime.settings.dev_mode:

        @router.post("/debug")
        def create_debug(body: DebugRequest) -> dict[str, Any]:
            return {"notifications": create_debug_notifications(service(), body.scenario)}

        @router.delete("/debug")
        def clear_debug() -> dict[str, Any]:
            try:
                return {"notifications": clear_debug_notifications(service())}
            except (NotificationConflict, NotificationNotFound) as exc:
                raise _error(exc) from exc

    @router.post("/{notification_id}/prepare")
    def prepare(notification_id: str, body: PrepareRequest) -> dict[str, Any]:
        try:
            item = service().prepare(
                notification_id, version=body.version, window_id=body.window_id
            )
            wire = item.to_wire()
            return {
                "notification": wire,
                "attempt": item.delivery["attempt"],
                "text": item.text,
                "channel": "pet",
                "navigation": wire["navigation"],
            }
        except (NotificationConflict, NotificationNotFound) as exc:
            raise _error(exc) from exc

    @router.post("/{notification_id}/presented")
    def presented(notification_id: str, body: PresentedRequest) -> dict[str, Any]:
        try:
            item, sound = service().presented(
                notification_id, attempt=body.attempt, text=body.text, truncated=body.truncated
            )
            return {"notification": item.to_wire(), "playSound": sound}
        except (NotificationConflict, NotificationNotFound) as exc:
            raise _error(exc) from exc

    @router.post("/{notification_id}/dismiss")
    def dismiss(notification_id: str, body: DismissRequest) -> dict[str, Any]:
        try:
            return {
                "notification": service()
                .dismiss(
                    notification_id,
                    version=body.version,
                    silent_replacement_ids=body.silent_replacement_ids,
                )
                .to_wire()
            }
        except (NotificationConflict, NotificationNotFound) as exc:
            raise _error(exc) from exc

    app.include_router(router)


def _error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(exc, NotificationNotFound) else 409, detail=str(exc)
    )
