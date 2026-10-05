"""Public, credential-free model route catalog."""

from __future__ import annotations

from agent.attachments.input_policy import AttachmentInputPolicy
from agent.context import (
    estimate_tokens,
    estimate_tool_schema_tokens,
    make_budget_snapshot,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from llm_providers.model_identity import is_custom_model_reference
from pydantic import BaseModel, ConfigDict, Field

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    ContextBaseOverBudgetError,
    Message,
)
from llm_providers import (
    ModelRuntimeConfig,
    all_model_ids,
    default_model_runtime_config,
    validate_model_runtime_config,
)

from ..assembly import BridgeRuntime
from ..errors import (
    UnsupportedModelError,
    UnsupportedReasoningPresetError,
    build_openai_error_envelope,
    map_exception,
)
from ..model_connections.catalog import connection_model_catalog


class ModelValidateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str = Field(..., description="目标 model id")
    reasoning_preset: str | None = Field(None, alias="reasoningPreset")


def register_routes(app: FastAPI, runtime: BridgeRuntime | None = None) -> None:
    @app.get("/v1/models/catalog")
    def model_catalog() -> dict[str, object]:
        catalog = connection_model_catalog(runtime.connection_runtime if runtime else None)
        if runtime is not None:
            for provider in catalog["providers"]:
                for model in provider["models"]:
                    model["attachmentInputs"] = AttachmentInputPolicy(
                        runtime.attachment_capabilities(model["id"]).tool_calling,
                        session_files_available=runtime.session_files_available,
                        **(
                            {"file_max_bytes": runtime.attachment_store.max_attachment_bytes}
                            if runtime.attachment_store
                            else {}
                        ),
                    ).public_payload()
        return catalog

    @app.post("/v1/models/validate")
    def validate_model(body: ModelValidateBody) -> object:
        try:
            if body.model not in all_model_ids() and not is_custom_model_reference(body.model):
                raise UnsupportedModelError(body.model)
            config = (
                ModelRuntimeConfig(body.model, body.reasoning_preset)
                if body.reasoning_preset is not None
                else default_model_runtime_config(body.model)
            )
            try:
                config = validate_model_runtime_config(config)
            except ValueError as exc:
                raise UnsupportedReasoningPresetError(str(exc)) from exc
            if runtime is None or runtime.llm_client_factory is None:
                raise RuntimeError("llm client factory unavailable")
            model = runtime.llm_client_factory(config)
            system_prompt = runtime.prompt_builder_factory(BUILTIN_DEFAULT_PERSONA_ID).build()
            tool_specs = runtime.tool_registry.to_openai_tools()
            baseline_tokens = estimate_tokens(
                [Message(role="system", content=system_prompt)]
            ) + estimate_tool_schema_tokens(tool_specs)
            threshold = make_budget_snapshot(model.context_window, None).trigger_threshold
            if baseline_tokens > threshold:
                raise ContextBaseOverBudgetError(
                    "default persona and tool schemas exceed the model context budget"
                )
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )
        return {
            "model": body.model,
            "available": True,
            "runtimeConfig": {
                "model": config.model_id,
                "reasoningPreset": config.reasoning_preset,
            },
            "limits": {
                "contextWindow": model.context_window,
                "contextWindowSource": "language_model",
                "safeInputThreshold": threshold,
                "baselineRequestTokens": baseline_tokens,
                "baselineHeadroomTokens": threshold - baseline_tokens,
            },
        }
