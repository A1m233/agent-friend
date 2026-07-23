"""Public, credential-free model route catalog."""

from __future__ import annotations

from fastapi import FastAPI

from llm_providers import public_model_catalog


def register_routes(app: FastAPI) -> None:
    @app.get("/v1/models/catalog")
    def model_catalog() -> dict[str, object]:
        return public_model_catalog()
