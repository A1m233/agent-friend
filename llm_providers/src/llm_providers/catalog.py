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

DEEPSEEK_PROVIDER_ID = "deepseek"
KIMI_PROVIDER_ID = "kimi"

DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
KIMI_API_KEY = "KIMI_API_KEY"

KIMI_API_BASE = "https://api.moonshot.cn/v1"


@dataclass(frozen=True)
class ProviderModel:
    """A user-visible model option and its LiteLLM call target."""

    id: str
    label: str
    litellm_model: str
    context_window: int | None = None


@dataclass(frozen=True)
class ProviderDefinition:
    """Provider-specific defaults used to assemble ``ProviderSpec``."""

    provider_id: str
    display_name: str
    credential_name: str
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
        models=(
            ProviderModel(
                id="kimi/kimi-k2.6",
                label="kimi-k2.6",
                litellm_model="openai/kimi-k2.6",
            ),
            ProviderModel(
                id="kimi/kimi-k2.5",
                label="kimi-k2.5",
                litellm_model="openai/kimi-k2.5",
            ),
            ProviderModel(
                id="kimi/kimi-k2.7-code",
                label="kimi-k2.7-code",
                litellm_model="openai/kimi-k2.7-code",
            ),
            ProviderModel(
                id="kimi/kimi-k2.7-code-highspeed",
                label="kimi-k2.7-code-highspeed",
                litellm_model="openai/kimi-k2.7-code-highspeed",
            ),
        ),
        default_model="kimi/kimi-k2.6",
        default_memory_model="kimi/kimi-k2.6",
        api_base=KIMI_API_BASE,
        default_call_options={"timeout": DEFAULT_LLM_TIMEOUT_SECONDS},
    ),
}


def normalize_provider_id(provider_id: str | None) -> str:
    value = (provider_id or DEEPSEEK_PROVIDER_ID).strip().lower()
    if value in ("deepseek", "deep-seek"):
        return DEEPSEEK_PROVIDER_ID
    if value in ("kimi", "moonshot"):
        return KIMI_PROVIDER_ID
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
    raise ValueError(f"无法根据 model 推断 provider: {model_id!r}")


def default_model_for_provider(provider_id: str, purpose: ModelPurpose = "chat") -> str:
    provider = provider_definition(provider_id)
    return provider.default_memory_model if purpose == "memory" else provider.default_model


def model_belongs_to_provider(provider_id: str, model_id: str) -> bool:
    provider = provider_definition(provider_id)
    return provider.model(model_id) is not None


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
    )


def public_provider_models() -> dict[str, tuple[str, ...]]:
    """Return safe model ids suitable for allowlists and UI mirroring."""

    return {
        provider_id: tuple(model.id for model in provider.models)
        for provider_id, provider in PROVIDER_DEFINITIONS.items()
    }


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
    )
