"""Runtime credential resolution for agent_bridge."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from llm_providers.model_identity import is_custom_model_reference

from llm_providers import (
    LanguageModel,
    LanguageModelCapabilities,
    LLMStreamEvent,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
    create_language_model,
    default_model_for_provider,
    get_model_capabilities,
    infer_provider_id_from_model,
    provider_definition,
)
from runtime_config import (
    AMAP_WEBSERVICE_KEY,
    LLM_CHAT_MODEL,
    LLM_CHAT_PROVIDER,
    LLM_MEMORY_MODEL,
    LLM_MEMORY_PROVIDER,
    QWEATHER_API_HOST,
    QWEATHER_API_KEY,
    TAVILY_API_KEY,
    CredentialSource,
    ToolCredentials,
    optional_secret,
    require_secret,
)

from .model_connections.runtime import ConnectionRuntime

DEFAULT_BRIDGE_CHAT_PROVIDER = "deepseek"
DEFAULT_BRIDGE_MEMORY_PROVIDER = "deepseek"


@dataclass(frozen=True)
class BridgeRuntimeConfig:
    """Credentials and provider-aware model assembly used by the bridge."""

    default_model: str
    memory_model: str
    memory_client_factory: Callable[[], LanguageModel]
    llm_client_factory: Callable[[ModelRuntimeConfig], LanguageModel]
    tool_credentials: ToolCredentials
    connection_runtime: ConnectionRuntime | None = None


class DeferredLanguageModel:
    """Delay credential resolution until code actually invokes the LLM.

    Bridge startup also owns local-only control-plane capabilities such as persona
    reset and migration inventory. Those capabilities must remain available before
    the destination machine has imported any provider credentials.
    """

    def __init__(self, factory: Callable[[], LanguageModel]) -> None:
        self._factory = factory
        self._model: LanguageModel | None = None

    def _get(self) -> LanguageModel:
        if self._model is None:
            self._model = self._factory()
        return self._model

    def bind_execution(self):  # type: ignore[no-untyped-def]
        from llm_providers.binding import bind_language_model

        return bind_language_model(self._factory())

    @property
    def capabilities(self) -> LanguageModelCapabilities:
        return get_model_capabilities(self._get())

    @property
    def context_window(self) -> int:
        return self._get().context_window

    def generate(self, request: ModelRequest) -> ModelResponse:
        return self._get().generate(request)

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        return self._get().stream(request)


def _optional_int(source: CredentialSource, name: str) -> int | None:
    raw = optional_secret(source, name)
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _optional_float(source: CredentialSource, name: str) -> float | None:
    raw = optional_secret(source, name)
    if not raw:
        return None
    try:
        parsed = float(raw)
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
    default_provider_id: str,
) -> tuple[str, str]:
    raw_provider_id = optional_secret(source, provider_name) or default_provider_id
    raw_model = optional_secret(source, model_name)
    if raw_provider_id == "custom":
        if raw_model is None or not is_custom_model_reference(raw_model):
            raise ValueError("Invalid custom model selection")
        return "custom", raw_model
    provider_id = provider_definition(raw_provider_id).provider_id
    model_id = optional_secret(source, model_name) or default_model_for_provider(
        provider_id, purpose
    )
    return provider_id, model_id


def _language_model(
    source: CredentialSource,
    *,
    provider_id: str,
    model_id: str,
    reasoning_preset: str | None = None,
    purpose: ModelPurpose,
) -> LanguageModel:
    from llm_providers.catalog import require_model_not_removed

    require_model_not_removed(model_id)
    provider = provider_definition(provider_id)
    prefix = _provider_prefix(provider.provider_id)
    return create_language_model(
        provider_id=provider.provider_id,
        api_key=require_secret(source, provider.credential_name),
        model_id=model_id,
        reasoning_preset=reasoning_preset,
        purpose=purpose,
        context_window=_optional_int(source, f"{prefix}_CONTEXT_WINDOW"),
        timeout_seconds=_optional_float(source, f"{prefix}_TIMEOUT_SECONDS"),
    )


def resolve_bridge_runtime_config(source: CredentialSource) -> BridgeRuntimeConfig:
    """Resolve bridge credentials from the configured source policy."""

    _chat_provider_id, default_model = _model_selection(
        source,
        provider_name=LLM_CHAT_PROVIDER,
        model_name=LLM_CHAT_MODEL,
        purpose="chat",
        default_provider_id=DEFAULT_BRIDGE_CHAT_PROVIDER,
    )
    _memory_provider_id, memory_model = _model_selection(
        source,
        provider_name=LLM_MEMORY_PROVIDER,
        model_name=LLM_MEMORY_MODEL,
        purpose="memory",
        default_provider_id=DEFAULT_BRIDGE_MEMORY_PROVIDER,
    )

    connection_runtime = ConnectionRuntime(source)

    def memory_selection() -> ModelRuntimeConfig:
        from llm_providers import default_model_runtime_config

        _provider, model = _model_selection(
            source,
            provider_name=LLM_MEMORY_PROVIDER,
            model_name=LLM_MEMORY_MODEL,
            purpose="memory",
            default_provider_id=DEFAULT_BRIDGE_MEMORY_PROVIDER,
        )
        from llm_providers.catalog import require_model_not_removed

        require_model_not_removed(model)
        preset = optional_secret(source, "LLM_MEMORY_REASONING_PRESET")
        if (
            preset is None
            and optional_secret(source, LLM_MEMORY_MODEL) is None
            and model == "deepseek/deepseek-flash"
        ):
            preset = "max"
        config = (
            ModelRuntimeConfig(model, preset)
            if preset is not None
            else default_model_runtime_config(model)
        )
        return connection_runtime.directory().validate(config)

    def memory_client_factory() -> LanguageModel:
        selection = memory_selection()
        if is_custom_model_reference(selection.model_id):
            return connection_runtime.model(memory_selection)
        return _language_model(
            source,
            provider_id=infer_provider_id_from_model(selection.model_id),
            model_id=selection.model_id,
            reasoning_preset=selection.reasoning_preset,
            purpose="memory",
        )

    def llm_client_factory(config: ModelRuntimeConfig) -> LanguageModel:
        if is_custom_model_reference(config.model_id):
            connection_runtime.directory().validate(config)
            return connection_runtime.model(lambda: config)
        provider_id = infer_provider_id_from_model(config.model_id)
        return _language_model(
            source,
            provider_id=provider_id,
            model_id=config.model_id,
            reasoning_preset=config.reasoning_preset,
            purpose="chat",
        )

    return BridgeRuntimeConfig(
        default_model=default_model,
        memory_model=memory_model,
        connection_runtime=connection_runtime,
        memory_client_factory=memory_client_factory,
        llm_client_factory=llm_client_factory,
        tool_credentials=ToolCredentials(
            tavily_api_key=optional_secret(source, TAVILY_API_KEY),
            amap_webservice_key=optional_secret(source, AMAP_WEBSERVICE_KEY),
            qweather_api_key=optional_secret(source, QWEATHER_API_KEY),
            qweather_api_host=optional_secret(source, QWEATHER_API_HOST),
        ),
    )
