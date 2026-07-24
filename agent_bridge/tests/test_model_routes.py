from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.models import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime_config import CredentialMissingError


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


def _validation_client(factory: object) -> TestClient:
    app = FastAPI()
    runtime = cast(BridgeRuntime, SimpleNamespace(llm_client_factory=factory))
    register_routes(app, runtime)
    return TestClient(app)


def test_model_validation_preflights_client_without_creating_session() -> None:
    seen: list[str] = []

    def factory(model: str) -> object:
        seen.append(model)
        return object()

    client = _validation_client(factory)

    response = client.post("/v1/models/validate", json={"model": "kimi/kimi-k2.6"})

    assert response.status_code == 200
    assert response.json() == {"model": "kimi/kimi-k2.6", "available": True}
    assert seen == ["kimi/kimi-k2.6"]


def test_model_validation_returns_stable_errors() -> None:
    unsupported = _validation_client(lambda _model: object()).post(
        "/v1/models/validate", json={"model": "unknown/model"}
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == "unsupported_model"

    def missing(_model: str) -> object:
        raise CredentialMissingError(["KIMI_API_KEY"])

    credential = _validation_client(missing).post(
        "/v1/models/validate", json={"model": "kimi/kimi-k2.6"}
    )
    assert credential.status_code == 503
    assert credential.json()["error"]["code"] == "credential_missing"
    assert "KIMI_API_KEY" not in credential.text
