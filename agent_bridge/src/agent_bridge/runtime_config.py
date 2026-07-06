"""Runtime credential resolution for agent_bridge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from llm_providers import (
    LLMClient,
    ModelPurpose,
    ProviderSpec,
    build_provider_spec,
    default_model_for_provider,
    infer_provider_id_from_model,
    provider_definition,
)
from runtime_config import (
    LLM_CHAT_MODEL,
    LLM_CHAT_PROVIDER,
    LLM_MEMORY_MODEL,
    LLM_MEMORY_PROVIDER,
    TAVILY_API_KEY,
    CredentialSource,
    ToolCredentials,
    optional_secret,
    require_secret,
)


@dataclass(frozen=True)
class BridgeRuntimeConfig:
    """Credentials and provider-aware model assembly used by the bridge."""

    default_model: str
    memory_spec: ProviderSpec
    llm_client_factory: Callable[[str], LLMClient]
    tool_credentials: ToolCredentials


def _optional_int(source: CredentialSource, name: str) -> int | None:
    raw = optional_secret(source, name)
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _provider_prefix(provider_id: str) -> str:
    credential_name = provider_definition(provider_id).credential_name
    return credential_name.removesuffix("_API_KEY")


def _model_selection(
    source: CredentialSource,
    *,
    provider_name: str,
    model_name: str,
    purpose: ModelPurpose,
) -> tuple[str, str]:
    raw_provider_id = optional_secret(source, provider_name) or "deepseek"
    provider_id = provider_definition(raw_provider_id).provider_id
    model_id = optional_secret(source, model_name) or default_model_for_provider(
        provider_id, purpose
    )
    return provider_id, model_id


def _provider_spec(
    source: CredentialSource,
    *,
    provider_id: str,
    model_id: str,
    purpose: ModelPurpose,
) -> ProviderSpec:
    provider = provider_definition(provider_id)
    prefix = _provider_prefix(provider.provider_id)
    return build_provider_spec(
        provider_id=provider.provider_id,
        api_key=require_secret(source, provider.credential_name),
        model_id=model_id,
        purpose=purpose,
        context_window=_optional_int(source, f"{prefix}_CONTEXT_WINDOW"),
    )


def resolve_bridge_runtime_config(source: CredentialSource) -> BridgeRuntimeConfig:
    """Resolve bridge credentials from the configured source policy."""

    chat_provider_id, default_model = _model_selection(
        source,
        provider_name=LLM_CHAT_PROVIDER,
        model_name=LLM_CHAT_MODEL,
        purpose="chat",
    )
    memory_provider_id, memory_model = _model_selection(
        source,
        provider_name=LLM_MEMORY_PROVIDER,
        model_name=LLM_MEMORY_MODEL,
        purpose="memory",
    )

    memory_spec = _provider_spec(
        source,
        provider_id=memory_provider_id,
        model_id=memory_model,
        purpose="memory",
    )

    def llm_client_factory(model_id: str) -> LLMClient:
        try:
            provider_id = infer_provider_id_from_model(model_id)
        except ValueError:
            provider_id = chat_provider_id
        return LLMClient(
            _provider_spec(
                source,
                provider_id=provider_id,
                model_id=model_id,
                purpose="chat",
            )
        )

    return BridgeRuntimeConfig(
        default_model=default_model,
        memory_spec=memory_spec,
        llm_client_factory=llm_client_factory,
        tool_credentials=ToolCredentials(tavily_api_key=optional_secret(source, TAVILY_API_KEY)),
    )
