from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.models import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_providers import LLMModelMetadataError, ModelRuntimeConfig
from runtime_config import CredentialMissingError


def test_model_catalog_route_projects_provider_capabilities() -> None:
    app = FastAPI()
    register_routes(app)

    response = TestClient(app).get("/v1/models/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == 2
    assert body["catalogRevision"]
    assert {provider["id"] for provider in body["providers"]} == {"deepseek", "kimi"}
    kimi = next(provider for provider in body["providers"] if provider["id"] == "kimi")
    k26 = next(model for model in kimi["models"] if model["id"] == "kimi/kimi-k2.6")
    assert k26["capabilities"]["attachments"] is True
    assert "application/pdf" in k26["capabilities"]["acceptedMediaTypes"]
    assert k26["limits"]["contextWindow"] == 256 * 1024
    assert k26["reasoning"]["defaultPreset"] == "on"
    deepseek = next(provider for provider in body["providers"] if provider["id"] == "deepseek")
    vision = next(
        model
        for model in deepseek["models"]
        if model["id"] == "deepseek/deepseek-v4-flash-vision-exp"
    )
    assert vision["label"] == "DeepSeek V4 Flash Vision"
    assert vision["processingProfile"] == "deepseek-vision-files-v1"
    assert vision["capabilities"]["inputModalities"] == ["text", "image"]
    assert vision["capabilities"]["acceptedMediaTypes"] == [
        "image/jpeg",
        "image/png",
        "image/webp",
    ]
    assert vision["capabilities"]["maxAttachmentBytes"] == 64 * 1024 * 1024
    assert vision["reasoning"] == {
        "control": "effort",
        "defaultPreset": "off",
        "presets": [
            {"id": "off", "label": "关闭"},
            {"id": "low", "label": "低"},
            {"id": "high", "label": "高"},
            {"id": "max", "label": "最高"},
        ],
    }
    assert deepseek["defaultChatModel"] == "deepseek/deepseek-v4-flash"
    assert deepseek["defaultMemoryModel"] == "deepseek/deepseek-v4-pro"


def _validation_client(factory: object) -> TestClient:
    app = FastAPI()
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            llm_client_factory=factory,
            prompt_builder_factory=lambda _persona_id: SimpleNamespace(build=lambda: "system"),
            tool_registry=SimpleNamespace(to_openai_tools=lambda: []),
        ),
    )
    register_routes(app, runtime)
    return TestClient(app)


def test_model_validation_preflights_client_without_creating_session() -> None:
    seen: list[ModelRuntimeConfig] = []

    def factory(model: ModelRuntimeConfig) -> object:
        seen.append(model)
        return SimpleNamespace(context_window=256 * 1024)

    client = _validation_client(factory)

    response = client.post("/v1/models/validate", json={"model": "kimi/kimi-k2.6"})

    assert response.status_code == 200
    assert response.json() == {
        "model": "kimi/kimi-k2.6",
        "available": True,
        "runtimeConfig": {"model": "kimi/kimi-k2.6", "reasoningPreset": "on"},
        "limits": {
            "contextWindow": 256 * 1024,
            "contextWindowSource": "language_model",
            "safeInputThreshold": 209716,
            "baselineRequestTokens": 4,
            "baselineHeadroomTokens": 209712,
        },
    }
    assert seen == [ModelRuntimeConfig("kimi/kimi-k2.6", "on")]


def test_model_validation_rejects_unsupported_reasoning_preset() -> None:
    response = _validation_client(lambda _config: SimpleNamespace(context_window=100_000)).post(
        "/v1/models/validate",
        json={"model": "kimi/kimi-k2.6", "reasoningPreset": "high"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_reasoning_preset"


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


def test_model_validation_rejects_unresolved_runtime_limits() -> None:
    class InvalidClient:
        @property
        def context_window(self) -> int:
            raise LLMModelMetadataError("missing context window")

    response = _validation_client(lambda _model: InvalidClient()).post(
        "/v1/models/validate", json={"model": "kimi/kimi-k2.6"}
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "model_context_profile_invalid"
