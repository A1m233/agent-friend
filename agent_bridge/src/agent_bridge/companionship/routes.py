from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from ..assembly import BridgeRuntime


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "enable", "disable", "pause_activity", "pause_contact", "resume_activity", "resume_contact"
    ]
    until: str | None = None


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1", tags=["companionship"])

    @router.get("/companionship")
    def status() -> dict[str, Any]:
        if runtime.companionship is None:
            raise HTTPException(status_code=503, detail="companionship unavailable")
        return runtime.companionship.status()

    @router.post("/companionship/inner-session")
    def inner_session() -> dict[str, str]:
        if runtime.companionship is None:
            raise HTTPException(status_code=503, detail="companionship unavailable")
        return {"session_id": runtime.companionship.ensure_inner()}

    @router.post("/companionship/control")
    def control(body: ControlBody) -> dict[str, Any]:
        if runtime.companionship is None:
            raise HTTPException(status_code=503, detail="companionship unavailable")
        try:
            return runtime.companionship.control(body.action, until=body.until, user_initiated=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/session-deliveries/{delivery_id}")
    def receipt(delivery_id: str) -> dict[str, Any]:
        if runtime.session_delivery is None:
            raise HTTPException(status_code=503, detail="session delivery unavailable")
        try:
            result = runtime.session_delivery.store.get(delivery_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error
        return {
            key: value
            for key, value in result.items()
            if key not in {"request", "fingerprint", "operation_key"}
        }

    app.include_router(router)
