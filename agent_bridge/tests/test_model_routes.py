from __future__ import annotations

from agent_bridge.routes.models import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_model_catalog_route_projects_provider_capabilities() -> None:
    app = FastAPI()
    register_routes(app)

    response = TestClient(app).get("/v1/models/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == 1
    assert body["catalogRevision"]
    kimi = next(provider for provider in body["providers"] if provider["id"] == "kimi")
    k26 = next(model for model in kimi["models"] if model["id"] == "kimi/kimi-k2.6")
    assert k26["capabilities"]["attachments"] is True
    assert "application/pdf" in k26["capabilities"]["acceptedMediaTypes"]
