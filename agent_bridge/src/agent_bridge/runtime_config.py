"""Runtime credential resolution for agent_bridge."""

from __future__ import annotations

from dataclasses import dataclass

from llm_providers import ProviderConfig
from runtime_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MEMORY_MODEL,
    DEEPSEEK_MODEL,
    TAVILY_API_KEY,
    CredentialSource,
    ToolCredentials,
    optional_secret,
    require_secret,
)


@dataclass(frozen=True)
class BridgeRuntimeConfig:
    """Credentials and optional provider overrides used at runtime assembly."""

    llm_config: ProviderConfig
    memory_llm_config: ProviderConfig
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


def _deepseek_provider_config(
    source: CredentialSource,
    *,
    model_name: str,
) -> ProviderConfig:
    return ProviderConfig(
        api_key=require_secret(source, DEEPSEEK_API_KEY),
        model=optional_secret(source, model_name),
        api_base=optional_secret(source, "DEEPSEEK_API_BASE"),
        context_window=_optional_int(source, "DEEPSEEK_CONTEXT_WINDOW"),
    )


def resolve_bridge_runtime_config(source: CredentialSource) -> BridgeRuntimeConfig:
    """Resolve bridge credentials from the configured source policy."""

    return BridgeRuntimeConfig(
        llm_config=_deepseek_provider_config(source, model_name=DEEPSEEK_MODEL),
        memory_llm_config=_deepseek_provider_config(
            source,
            model_name=DEEPSEEK_MEMORY_MODEL,
        ),
        tool_credentials=ToolCredentials(tavily_api_key=optional_secret(source, TAVILY_API_KEY)),
    )
