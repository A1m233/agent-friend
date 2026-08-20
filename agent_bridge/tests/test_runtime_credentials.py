from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.lifecycle import BridgeLifecycleController
from agent_bridge.runtime_credentials import register_runtime_credential_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_providers import CredentialValidationResult
from runtime_config import (
    CredentialStatus,
    CredentialValue,
    runtime_credential_stage_name,
    runtime_credential_stage_operation_name,
)

Validator = Callable[[str, str], CredentialValidationResult]


def _valid(_provider: str, _value: str) -> CredentialValidationResult:
    return CredentialValidationResult("valid", "credential_accepted")


class _Source:
    source_name = "test-keyring"

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> CredentialValue | None:
        value = self.values.get(name)
        return CredentialValue(name=name, value=value, source=self.source_name) if value else None

    def status(self, name: str) -> CredentialStatus:
        value = self.get(name)
        return CredentialStatus(name=name, configured=value is not None, source=self.source_name)


def _client(
    group_id: str,
    fields: dict[str, str],
    *,
    validator: Validator = _valid,
) -> tuple[TestClient, str]:
    operation_id = "operation-1"
    values = {runtime_credential_stage_operation_name(group_id): operation_id}
    values.update(
        {runtime_credential_stage_name(group_id, name): value for name, value in fields.items()}
    )
    app = FastAPI()
    controller = BridgeLifecycleController(
        cast(BridgeRuntime, SimpleNamespace()),
        token="owner-token",
    )
    register_runtime_credential_routes(
        app,
        controller,
        source=_Source(values),
        validator=validator,
    )
    return TestClient(app), operation_id


def test_deepseek_validation_reads_staging_and_returns_safe_result() -> None:
    seen: list[tuple[str, str]] = []

    def record_validation(provider: str, value: str) -> CredentialValidationResult:
        seen.append((provider, value))
        return CredentialValidationResult("valid", "credential_accepted")

    client, operation_id = _client(
        "deepseek",
        {"DEEPSEEK_API_KEY": "fixture-secret"},
        validator=record_validation,
    )

    response = client.post(
        "/internal/runtime-credentials/validate",
        json={"groupId": "deepseek", "operationId": operation_id},
        headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"result": "valid", "safeCode": "credential_accepted"}
    assert "fixture-secret" not in response.text
    assert seen == [("deepseek", "fixture-secret")]


def test_openrouter_validation_uses_staged_key_and_provider_route() -> None:
    seen: list[tuple[str, str]] = []

    def record_validation(provider: str, value: str) -> CredentialValidationResult:
        seen.append((provider, value))
        return CredentialValidationResult("valid", "credential_accepted")

    client, operation_id = _client(
        "openrouter",
        {"OPENROUTER_API_KEY": "fixture-secret"},
        validator=record_validation,
    )

    response = client.post(
        "/internal/runtime-credentials/validate",
        json={"groupId": "openrouter", "operationId": operation_id},
        headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
    )

    assert response.json() == {"result": "valid", "safeCode": "credential_accepted"}
    assert seen == [("openrouter", "fixture-secret")]
    assert "fixture-secret" not in response.text


def test_unsupported_group_is_truthful_without_online_validator() -> None:
    client, operation_id = _client(
        "tavily",
        {"TAVILY_API_KEY": "fixture-secret"},
        validator=lambda *_: (_ for _ in ()).throw(AssertionError("must not validate online")),
    )
    response = client.post(
        "/internal/runtime-credentials/validate",
        json={"groupId": "tavily", "operationId": operation_id},
        headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
    )
    assert response.json() == {"result": "unsupported", "safeCode": "validation_unsupported"}


def test_missing_required_field_and_stale_operation_are_rejected_safely() -> None:
    client, operation_id = _client("qWeather", {"QWEATHER_API_KEY": "fixture-secret"})
    incomplete = client.post(
        "/internal/runtime-credentials/validate",
        json={"groupId": "qWeather", "operationId": operation_id},
        headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
    )
    stale = client.post(
        "/internal/runtime-credentials/validate",
        json={"groupId": "qWeather", "operationId": "stale"},
        headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
    )
    assert incomplete.json() == {"result": "invalid", "safeCode": "invalid_candidate"}
    assert stale.status_code == 409


def test_validation_route_requires_lifecycle_token() -> None:
    client, operation_id = _client(
        "kimi",
        {"KIMI_API_KEY": "fixture-secret"},
    )
    response = client.post(
        "/internal/runtime-credentials/validate",
        json={"groupId": "kimi", "operationId": operation_id},
    )
    assert response.status_code == 401
