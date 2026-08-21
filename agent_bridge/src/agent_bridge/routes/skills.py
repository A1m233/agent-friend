"""Read-only Skill catalog diagnostics and UI discovery route."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException

from ..assembly import BridgeRuntime


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter()

    @router.get("/v1/skills")
    def list_skills() -> dict[str, object]:
        if runtime.skill_service is None:
            raise HTTPException(status_code=503, detail="Skill catalog unavailable")
        payload = runtime.skill_service.catalog_payload()
        if runtime.first_party_skill_updater is not None:
            payload["firstPartyUpdates"] = runtime.first_party_skill_updater.diagnostic().to_dict()
        return payload

    app.include_router(router)
