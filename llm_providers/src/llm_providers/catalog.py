"""Provider catalog for LLM runtime assembly.

This module owns provider-specific knowledge: credential names, default models,
LiteLLM model ids, base URLs, and per-provider call options. Callers such as
``agent_bridge`` should ask for a ``ProviderSpec`` instead of branching on
DeepSeek / Kimi themselves.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import LLMAuthError
from .spec import DEFAULT_LLM_TIMEOUT_SECONDS, ProviderSpec

ModelPurpose = Literal["chat", "memory"]
InputModality = Literal["text", "image", "document"]

CATALOG_REVISION = "2026-08-20.1"

DEEPSEEK_PROVIDER_ID = "deepseek"
KIMI_PROVIDER_ID = "kimi"
OPENROUTER_PROVIDER_ID = "openrouter"

DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
KIMI_API_KEY = "KIMI_API_KEY"
OPENROUTER_API_KEY = "OPENROUTER_API_KEY"

KIMI_API_BASE = "https://api.moonshot.cn/v1"
KIMI_CONTEXT_WINDOW = 256 * 1024


@dataclass(frozen=True)
class ModelRouteCapabilities:
    """Product-visible capabilities for one concrete model route.

    This is the single source of truth used by the bridge, agent runtime and UI.
    Provider adapters may have additional internal limits, but callers must not
    maintain another model capability table.
    """

    input_modalities: tuple[InputModality, ...] = ("text",)
    accepted_media_types: tuple[str, ...] = ()
    max_attachment_bytes: int = 0
    max_total_attachment_bytes: int = 0
    max_attachments_per_turn: int = 0
    tool_calling: bool = True
    parallel_tool_calls: bool = True
    structured_output: bool = False
    multimodal_tool_results: bool = False
    reasoning_replay: bool = False

    @property
    def attachments(self) -> bool:
        return any(modality != "text" for modality in self.input_modalities)


TEXT_ONLY_CAPABILITIES = ModelRouteCapabilities()
KIMI_MULTIMODAL_CAPABILITIES = ModelRouteCapabilities(
    input_modalities=("text", "image", "document"),
    accepted_media_types=("image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"),
    max_attachment_bytes=100 * 1024 * 1024,
    max_total_attachment_bytes=100 * 1024 * 1024,
    max_attachments_per_turn=10,
)


@dataclass(frozen=True)
class ProviderModel:
    """A user-visible model option and its LiteLLM call target."""

    id: str
    label: str
    litellm_model: str
    context_window: int
    capabilities: ModelRouteCapabilities = TEXT_ONLY_CAPABILITIES


@dataclass(frozen=True)
class ProviderDefinition:
    """Provider-specific defaults used to assemble ``ProviderSpec``."""

    provider_id: str
    display_name: str
    credential_name: str
    credential_group: str
    models: tuple[ProviderModel, ...]
    default_model: str
    default_memory_model: str
    api_base: str | None = None
    default_call_options: Mapping[str, Any] = field(default_factory=dict)

    def model(self, model_id: str) -> ProviderModel | None:
        for option in self.models:
            if option.id == model_id:
                return option
        return None


PROVIDER_DEFINITIONS: dict[str, ProviderDefinition] = {
    DEEPSEEK_PROVIDER_ID: ProviderDefinition(
        provider_id=DEEPSEEK_PROVIDER_ID,
        display_name="DeepSeek",
        credential_name=DEEPSEEK_API_KEY,
        credential_group=DEEPSEEK_PROVIDER_ID,
        models=(
            ProviderModel(
                id="deepseek/deepseek-v4-flash",
                label="deepseek/deepseek-v4-flash",
                litellm_model="deepseek/deepseek-v4-flash",
                context_window=1_000_000,
            ),
            ProviderModel(
                id="deepseek/deepseek-v4-pro",
                label="deepseek/deepseek-v4-pro",
                litellm_model="deepseek/deepseek-v4-pro",
                context_window=1_000_000,
            ),
        ),
        default_model="deepseek/deepseek-v4-flash",
        default_memory_model="deepseek/deepseek-v4-pro",
        default_call_options={
            "extra_body": {"thinking": {"type": "disabled"}},
            "timeout": DEFAULT_LLM_TIMEOUT_SECONDS,
        },
    ),
    KIMI_PROVIDER_ID: ProviderDefinition(
        provider_id=KIMI_PROVIDER_ID,
        display_name="Kimi",
        credential_name=KIMI_API_KEY,
        credential_group=KIMI_PROVIDER_ID,
        models=(
            ProviderModel(
                id="kimi/kimi-k2.6",
                label="kimi-k2.6",
                litellm_model="openai/kimi-k2.6",
                context_window=KIMI_CONTEXT_WINDOW,
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            ),
            ProviderModel(
                id="kimi/kimi-k2.5",
                label="kimi-k2.5",
                litellm_model="openai/kimi-k2.5",
                context_window=KIMI_CONTEXT_WINDOW,
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            ),
            ProviderModel(
                id="kimi/kimi-k2.7-code",
                label="kimi-k2.7-code",
                litellm_model="openai/kimi-k2.7-code",
                context_window=KIMI_CONTEXT_WINDOW,
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            ),
            ProviderModel(
                id="kimi/kimi-k2.7-code-highspeed",
                label="kimi-k2.7-code-highspeed",
                litellm_model="openai/kimi-k2.7-code-highspeed",
                context_window=KIMI_CONTEXT_WINDOW,
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            ),
        ),
        default_model="kimi/kimi-k2.6",
        default_memory_model="kimi/kimi-k2.6",
        api_base=KIMI_API_BASE,
        default_call_options={"timeout": DEFAULT_LLM_TIMEOUT_SECONDS},
    ),
    OPENROUTER_PROVIDER_ID: ProviderDefinition(
        provider_id=OPENROUTER_PROVIDER_ID,
        display_name="OpenRouter",
        credential_name=OPENROUTER_API_KEY,
        credential_group=OPENROUTER_PROVIDER_ID,
        models=(
            ProviderModel(
                id="openrouter/google/gemini-3.7-flash",
                label="Google · Gemini 3.7 Flash",
                litellm_model="openrouter/google/gemini-3.7-flash",
                context_window=1_048_576,
            ),
            ProviderModel(
                id="openrouter/openai/gpt-5.6-luna",
                label="OpenAI · GPT-5.6 Luna",
                litellm_model="openrouter/openai/gpt-5.6-luna",
                context_window=1_050_000,
            ),
        ),
        default_model="openrouter/google/gemini-3.7-flash",
        default_memory_model="openrouter/openai/gpt-5.6-luna",
        default_call_options={
            "extra_body": {
                "provider": {
                    "require_parameters": True,
                    "data_collection": "deny",
                }
            },
            "timeout": DEFAULT_LLM_TIMEOUT_SECONDS,
        },
    ),
}


def normalize_provider_id(provider_id: str | None) -> str:
    value = (provider_id or DEEPSEEK_PROVIDER_ID).strip().lower()
    if value in ("deepseek", "deep-seek"):
        return DEEPSEEK_PROVIDER_ID
    if value in ("kimi", "moonshot"):
        return KIMI_PROVIDER_ID
    if value in ("openrouter", "open-router"):
        return OPENROUTER_PROVIDER_ID
    raise ValueError(f"不支持的 LLM provider: {provider_id!r}")


def provider_definition(provider_id: str | None) -> ProviderDefinition:
    normalized = normalize_provider_id(provider_id)
    return PROVIDER_DEFINITIONS[normalized]


def all_model_ids() -> frozenset[str]:
    return frozenset(
        option.id for provider in PROVIDER_DEFINITIONS.values() for option in provider.models
    )


def infer_provider_id_from_model(model_id: str) -> str:
    """Infer the provider that owns a canonical model id."""

    model = model_id.strip()
    for provider in PROVIDER_DEFINITIONS.values():
        if provider.model(model) is not None:
            return provider.provider_id
    if model.startswith("deepseek/"):
        return DEEPSEEK_PROVIDER_ID
    if model.startswith("kimi/"):
        return KIMI_PROVIDER_ID
    if model.startswith("openrouter/"):
        return OPENROUTER_PROVIDER_ID
    raise ValueError(f"无法根据 model 推断 provider: {model_id!r}")


def default_model_for_provider(provider_id: str, purpose: ModelPurpose = "chat") -> str:
    provider = provider_definition(provider_id)
    return provider.default_memory_model if purpose == "memory" else provider.default_model


def model_belongs_to_provider(provider_id: str, model_id: str) -> bool:
    provider = provider_definition(provider_id)
    return provider.model(model_id) is not None


def model_capabilities(model_id: str) -> ModelRouteCapabilities:
    provider = provider_definition(infer_provider_id_from_model(model_id))
    model = provider.model(model_id)
    return model.capabilities if model is not None else TEXT_ONLY_CAPABILITIES


def _fallback_litellm_model(provider: ProviderDefinition, model_id: str) -> str:
    if provider.provider_id == KIMI_PROVIDER_ID and model_id.startswith("kimi/"):
        return "openai/" + model_id.removeprefix("kimi/")
    return model_id


def build_provider_spec(
    *,
    provider_id: str,
    api_key: str,
    model_id: str | None = None,
    purpose: ModelPurpose = "chat",
    context_window: int | None = None,
    timeout_seconds: float | None = None,
) -> ProviderSpec:
    """Build a ``ProviderSpec`` from a provider selection and API key."""

    provider = provider_definition(provider_id)
    key = api_key.strip()
    if not key:
        raise LLMAuthError(f"{provider.credential_name} 未配置。请先配置 provider credential。")

    selected_model = model_id or default_model_for_provider(provider.provider_id, purpose)
    model = provider.model(selected_model)
    litellm_model = (
        model.litellm_model if model else _fallback_litellm_model(provider, selected_model)
    )
    model_window = model.context_window if model else None

    defaults = dict(provider.default_call_options)
    if timeout_seconds is not None:
        defaults["timeout"] = timeout_seconds

    return ProviderSpec(
        model=litellm_model,
        api_key=key,
        api_base=provider.api_base,
        defaults=defaults,
        context_window=context_window or model_window,
        context_window_source=(
            "runtime_override" if context_window is not None else "catalog" if model else None
        ),
    )


def public_provider_models() -> dict[str, tuple[str, ...]]:
    """Return safe model ids suitable for allowlists and UI mirroring."""

    return {
        provider_id: tuple(model.id for model in provider.models)
        for provider_id, provider in PROVIDER_DEFINITIONS.items()
    }


def public_model_catalog() -> dict[str, Any]:
    """Return the credential-free model route catalog for public clients."""

    providers: list[dict[str, Any]] = []
    for provider in PROVIDER_DEFINITIONS.values():
        models: list[dict[str, Any]] = []
        for model in provider.models:
            capabilities = dataclasses.asdict(model.capabilities)
            capabilities["attachments"] = model.capabilities.attachments
            models.append(
                {
                    "id": model.id,
                    "label": model.label,
                    "routeId": f"{provider.provider_id}:{model.id}:openai-compatible",
                    "transportProfile": "openai-compatible",
                    "processingProfile": (
                        "kimi-multimodal-v1" if model.capabilities.attachments else "text-only-v1"
                    ),
                    "limits": {
                        "contextWindow": model.context_window,
                        "contextWindowSource": "catalog",
                    },
                    "capabilities": {
                        "inputModalities": capabilities["input_modalities"],
                        "acceptedMediaTypes": capabilities["accepted_media_types"],
                        "maxAttachmentBytes": capabilities["max_attachment_bytes"],
                        "maxTotalAttachmentBytes": capabilities["max_total_attachment_bytes"],
                        "maxAttachmentsPerTurn": capabilities["max_attachments_per_turn"],
                        "attachments": capabilities["attachments"],
                        "toolCalling": capabilities["tool_calling"],
                        "parallelToolCalls": capabilities["parallel_tool_calls"],
                        "structuredOutput": capabilities["structured_output"],
                        "multimodalToolResults": capabilities["multimodal_tool_results"],
                        "reasoningReplay": capabilities["reasoning_replay"],
                    },
                }
            )
        providers.append(
            {
                "id": provider.provider_id,
                "label": provider.display_name,
                "credentialGroup": provider.credential_group,
                "defaultChatModel": provider.default_model,
                "defaultMemoryModel": provider.default_memory_model,
                "models": models,
            }
        )
    return {"schemaVersion": 1, "catalogRevision": CATALOG_REVISION, "providers": providers}


def replace_spec_model(spec: ProviderSpec, model_id: str) -> ProviderSpec:
    """Return ``spec`` with model/api_base/defaults adjusted for ``model_id``.

    This helper keeps compatibility with places that have already resolved the
    provider credentials but need to bind a session-specific model.
    """

    provider = provider_definition(infer_provider_id_from_model(model_id))
    model = provider.model(model_id)
    defaults = dict(provider.default_call_options)
    if "timeout" in spec.defaults:
        defaults["timeout"] = spec.defaults["timeout"]

    return dataclasses.replace(
        spec,
        model=model.litellm_model if model else _fallback_litellm_model(provider, model_id),
        api_base=provider.api_base,
        defaults=defaults,
        context_window=model.context_window if model else spec.context_window,
        context_window_source="catalog" if model else spec.context_window_source,
    )
