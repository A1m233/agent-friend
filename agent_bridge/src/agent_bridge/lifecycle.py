"""Owner-only lifecycle control plane for the desktop service supervisor."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .assembly import BridgeRuntime

LIFECYCLE_TOKEN_ENV = "AGENT_FRIEND_LIFECYCLE_TOKEN"


class DrainPayload(BaseModel):
    grace_timeout: float = Field(default=5.0, ge=0.0, le=60.0)
    cancel_timeout: float = Field(default=5.0, ge=0.0, le=60.0)


class BridgeLifecycleController:
    def __init__(
        self,
        runtime: BridgeRuntime,
        *,
        token: str,
        shutdown_callback: Callable[[], None] | None = None,
    ) -> None:
        if not token:
            raise ValueError("lifecycle token cannot be empty")
        self.runtime = runtime
        self._token = token
        self._shutdown_callback = shutdown_callback

    def set_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self._shutdown_callback = callback

    def authorize(self, supplied: str | None) -> None:
        if supplied is None or not secrets.compare_digest(self._token, supplied):
            raise HTTPException(status_code=401, detail="invalid lifecycle authorization")

    def readiness(self) -> dict[str, object]:
        agent_runtime = self.runtime.agent_runtime
        activity = agent_runtime.activity_snapshot() if agent_runtime is not None else None
        ready = activity is not None and activity.accepting
        return {
            "status": "ready" if ready else "not_ready",
            "service": "bridge",
            "accepting": activity.accepting if activity is not None else False,
            "checks": {
                "runtime": "ready" if ready else "not_ready",
                "routes": "ready",
            },
        }

    def quiesce(self) -> dict[str, object]:
        if self.runtime.agent_runtime is None:
            raise HTTPException(status_code=503, detail="agent runtime is unavailable")
        return asdict(self.runtime.agent_runtime.quiesce())

    def drain(self, payload: DrainPayload) -> dict[str, object]:
        if self.runtime.agent_runtime is None:
            raise HTTPException(status_code=503, detail="agent runtime is unavailable")
        report = self.runtime.agent_runtime.drain(
            grace_timeout=payload.grace_timeout,
            cancel_timeout=payload.cancel_timeout,
        )
        return asdict(report)

    def shutdown(self, payload: DrainPayload) -> dict[str, object]:
        report = self.drain(payload)
        if report["timed_out"]:
            raise HTTPException(status_code=409, detail={"code": "drain_timeout", **report})
        if self._shutdown_callback is None:
            raise HTTPException(status_code=503, detail="controlled shutdown is unavailable")
        self._shutdown_callback()
        return {"status": "shutting_down", "drain": report}


def register_lifecycle_routes(app: FastAPI, controller: BridgeLifecycleController) -> None:
    router = APIRouter(prefix="/internal/lifecycle", include_in_schema=False)

    def authorize(x_agent_friend_lifecycle_token: str | None = Header(default=None)) -> None:
        controller.authorize(x_agent_friend_lifecycle_token)

    @router.get("/readiness", dependencies=[])
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

    @router.post("/drain")
    def drain(
        payload: DrainPayload,
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        return controller.drain(payload)

    @router.post("/shutdown")
    def shutdown(
        payload: DrainPayload,
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        return controller.shutdown(payload)

    app.include_router(router)
