"""Tauri 桌面 presence 上报与 Reminder 路由诊断。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from ..assembly import BridgeRuntime
from ..presence import DesktopPresenceSnapshot


class DesktopPresenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime = Field(alias="observedAt")
    current_session_id: str | None = Field(default=None, alias="currentSessionId")
    window_visible: bool = Field(alias="windowVisible")
    window_focused: bool = Field(alias="windowFocused")
    window_minimized: bool = Field(alias="windowMinimized")
    os_idle_ms: int | None = Field(default=None, alias="osIdleMs", ge=0)
    session_state: Literal["active", "locked", "sleeping", "unknown"] = Field(alias="sessionState")
    last_desktop_interaction_at: datetime | None = Field(
        default=None, alias="lastDesktopInteractionAt"
    )
    capabilities: list[str] = Field(default_factory=list)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/runtime", tags=["runtime-presence"])

    @router.post("/presence/desktop")
    def update_desktop(payload: DesktopPresenceRequest) -> dict[str, str]:
        if runtime.presence_registry is None:
            return {"status": "disabled"}
        runtime.presence_registry.update_desktop(
            DesktopPresenceSnapshot(
                observed_at=_aware(payload.observed_at),
                current_session_id=payload.current_session_id,
                window_visible=payload.window_visible,
                window_focused=payload.window_focused,
                window_minimized=payload.window_minimized,
                os_idle_ms=payload.os_idle_ms,
                session_state=payload.session_state,
                last_desktop_interaction_at=(
                    _aware(payload.last_desktop_interaction_at)
                    if payload.last_desktop_interaction_at
                    else None
                ),
                capabilities=tuple(payload.capabilities),
            )
        )
        return {"status": "ok"}

    @router.get("/reminder-diagnostics")
    def diagnostics() -> dict[str, object]:
        if runtime.presence_registry is None:
            return {"status": "disabled"}
        subscribers = (
            runtime.agent_runtime.listeners.subscriber_count()
            if runtime.agent_runtime is not None
            else 0
        )
        return {
            "status": "ok",
            "presence": runtime.presence_registry.diagnostics(
                now=datetime.now(UTC), pet_available=subscribers > 0
            ),
            "occurrences": [
                {
                    "id": item.id,
                    "state": item.state.value,
                    "scheduledAtUtc": item.scheduled_at_utc.isoformat(),
                    "destinationKind": item.destination_kind,
                    "agentResponseStatus": item.agent_response_status.value,
                    "attemptCount": item.attempt_count,
                    "invalidatedAt": (
                        item.invalidated_at.isoformat() if item.invalidated_at else None
                    ),
                    "invalidationReason": item.invalidation_reason,
                }
                for item in (runtime.todo_store.diagnostics() if runtime.todo_store else [])
            ],
        }

    app.include_router(router)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["register_routes"]
