"""Owner-only runtime credential validation control plane."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from llm_providers import CredentialValidationResult, validate_provider_credential
from runtime_config import (
    DEEPSEEK_API_KEY,
    KIMI_API_KEY,
    OPENROUTER_API_KEY,
    CredentialSource,
    KeyringSource,
    runtime_credential_bundle,
    runtime_credential_stage_name,
    runtime_credential_stage_operation_name,
)

from .lifecycle import BridgeLifecycleController

ValidationResult = Literal["valid", "invalid", "temporarily_unavailable", "unsupported"]

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "deepseek": frozenset({DEEPSEEK_API_KEY}),
    "kimi": frozenset({KIMI_API_KEY}),
    "openrouter": frozenset({OPENROUTER_API_KEY}),
    "tavily": frozenset({"TAVILY_API_KEY"}),
    "amap": frozenset({"AMAP_WEBSERVICE_KEY"}),
    "qWeather": frozenset({"QWEATHER_API_KEY", "QWEATHER_API_HOST"}),
    "volcVoice": frozenset(
        {
            "VOLC_ACCESS_KEY",
            "VOLC_SECRET_KEY",
            "VOLC_RTC_APP_ID",
            "VOLC_RTC_APP_KEY",
            "VOLC_SPEECH_APP_ID",
            "VOLC_SPEECH_ACCESS_TOKEN",
        }
    ),
}
_ONLINE_PROVIDERS = {
    "deepseek": (DEEPSEEK_API_KEY, "deepseek"),
    "kimi": (KIMI_API_KEY, "kimi"),
    "openrouter": (OPENROUTER_API_KEY, "openrouter"),
}


class RuntimeCredentialValidationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    group_id: str = Field(alias="groupId")
    operation_id: str = Field(alias="operationId", min_length=1, max_length=128)


def _staged_values(
    source: CredentialSource,
    *,
    group_id: str,
    operation_id: str,
) -> dict[str, str]:
    bundle = runtime_credential_bundle(group_id)
    if bundle is None:
        raise HTTPException(status_code=400, detail={"code": "unknown_credential_group"})
    marker = source.get(runtime_credential_stage_operation_name(group_id))
    if marker is None or marker.value != operation_id:
        raise HTTPException(status_code=409, detail={"code": "staging_operation_mismatch"})
    values: dict[str, str] = {}
    for name in bundle:
        staged = source.get(runtime_credential_stage_name(group_id, name))
        if staged is not None:
            values[name] = staged.value
    return values


def _validate_staged_group(
    source: CredentialSource,
    payload: RuntimeCredentialValidationPayload,
    validator: Callable[[str, str], CredentialValidationResult],
) -> dict[str, str]:
    values = _staged_values(
        source,
        group_id=payload.group_id,
        operation_id=payload.operation_id,
    )
    missing = sorted(_REQUIRED_FIELDS[payload.group_id] - values.keys())
    if missing:
        return {"result": "invalid", "safeCode": "invalid_candidate"}
    provider = _ONLINE_PROVIDERS.get(payload.group_id)
    if provider is None:
        return {"result": "unsupported", "safeCode": "validation_unsupported"}
    field_name, provider_id = provider
    result = validator(provider_id, values[field_name])
    return {"result": result.kind, "safeCode": result.safe_code}


def register_runtime_credential_routes(
    app: FastAPI,
    lifecycle: BridgeLifecycleController,
    *,
    source: CredentialSource | None = None,
    validator: Callable[[str, str], CredentialValidationResult] = validate_provider_credential,
) -> None:
    """Register a lifecycle-token protected route that never receives a secret."""

    credential_source = source or KeyringSource()
    router = APIRouter(prefix="/internal/runtime-credentials", include_in_schema=False)

    @router.post("/validate")
    def validate(
        payload: RuntimeCredentialValidationPayload,
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        lifecycle.authorize(x_agent_friend_lifecycle_token)
        return _validate_staged_group(credential_source, payload, validator)

    app.include_router(router)
