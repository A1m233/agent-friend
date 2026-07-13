"""Owner-only lifecycle control plane for voice bridge supervision."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import APIRouter, FastAPI, Header, HTTPException

from .assembly import VoiceBridgeRuntime

LIFECYCLE_TOKEN_ENV = "AGENT_FRIEND_LIFECYCLE_TOKEN"


class VoiceLifecycleController:
    def __init__(
        self,
        runtime: VoiceBridgeRuntime,
        *,
        token: str,
        shutdown_callback: Callable[[], None] | None = None,
    ) -> None:
        if not token:
            raise ValueError("lifecycle token cannot be empty")
        self.runtime = runtime
        self._token = token
        self._shutdown_callback = shutdown_callback
        self._quiescing = False

    def set_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self._shutdown_callback = callback

    def authorize(self, supplied: str | None) -> None:
        if supplied is None or not secrets.compare_digest(self._token, supplied):
            raise HTTPException(status_code=401, detail="invalid lifecycle authorization")

    def readiness(self) -> dict[str, object]:
        activity = self.runtime.call_registry.activity_snapshot()
        return {
            "status": "not_ready" if self._quiescing else "ready",
            "service": "voice",
            "accepting": not self._quiescing,
            "checks": {"runtime": "ready", "routes": "ready"},
            "activity": {
                "pendingCallIds": activity.pending_call_ids,
                "activeCallIds": activity.active_call_ids,
            },
        }

    def quiesce(self) -> dict[str, object]:
        activity = self.runtime.call_registry.activity_snapshot()
        if activity.blocks_restart:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "voice_activity_blocked",
                    "pendingCallIds": activity.pending_call_ids,
                    "activeCallIds": activity.active_call_ids,
                },
            )
        self._quiescing = True
        return {"accepting": False, "activityBlocked": False}

    def shutdown(self) -> dict[str, object]:
        self.quiesce()
        if self._shutdown_callback is None:
            raise HTTPException(status_code=503, detail="controlled shutdown is unavailable")
        self._shutdown_callback()
        return {"status": "shutting_down"}


def register_lifecycle_routes(app: FastAPI, controller: VoiceLifecycleController) -> None:
    router = APIRouter(prefix="/internal/lifecycle", include_in_schema=False)

    def authorize(supplied: str | None) -> None:
        controller.authorize(supplied)

    @router.get("/readiness")
    def readiness(
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        return controller.readiness()

    @router.post("/quiesce")
    def quiesce(
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        return controller.quiesce()

    @router.post("/shutdown")
    def shutdown(
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        return controller.shutdown()

    app.include_router(router)
