"""Public, credential-free model route catalog."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from llm_providers import all_model_ids, public_model_catalog

from ..assembly import BridgeRuntime
from ..errors import UnsupportedModelError, build_openai_error_envelope, map_exception


class ModelValidateBody(BaseModel):
    model: str = Field(..., description="目标 model id")


def register_routes(app: FastAPI, runtime: BridgeRuntime | None = None) -> None:
    @app.get("/v1/models/catalog")
    def model_catalog() -> dict[str, object]:
        return public_model_catalog()

    @app.post("/v1/models/validate")
    def validate_model(body: ModelValidateBody) -> object:
        try:
            if body.model not in all_model_ids():
                raise UnsupportedModelError(body.model)
            if runtime is None or runtime.llm_client_factory is None:
                raise RuntimeError("llm client factory unavailable")
            runtime.llm_client_factory(body.model)
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )
        return {"model": body.model, "available": True}
