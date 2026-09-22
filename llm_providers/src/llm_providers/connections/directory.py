"""Read-only per-runtime catalog; no global registration or credential lookup."""

from collections.abc import Iterable
from dataclasses import replace
from types import MappingProxyType
from urllib.parse import urlsplit

from ..catalog import (
    CUSTOM_REASONING,
    KIMI_MULTIMODAL_CAPABILITIES,
    PROVIDER_DEFINITIONS,
    InputModality,
    ModelRouteCapabilities,
    ModelRuntimeConfig,
    ProviderDefinition,
    ProviderModel,
    build_provider_spec,
)
from ..custom_reasoning import custom_effort
from ..errors import LLMAuthError, LLMModelUnavailableError
from ..model_identity import is_custom_model_reference
from ..spec import DEFAULT_LLM_TIMEOUT_SECONDS, ProviderSpec, ResponsesRoute
from .definitions import ConnectionDefinition, model_reference


class ModelDirectory:
    """Bind availability and metadata to one immutable configuration revision."""

    def __init__(self, connections: Iterable[ConnectionDefinition] = ()) -> None:
        definitions = tuple(connections)
        if len({item.id for item in definitions}) != len(definitions):
            raise ValueError("Duplicate connection identity")
        self.connections = MappingProxyType({item.id: item for item in definitions})
        owners: dict[str, ProviderDefinition | ConnectionDefinition] = {}
        models: dict[str, ProviderModel] = {}
        for provider in PROVIDER_DEFINITIONS.values():
            for model in provider.models:
                owners[model.id] = provider
                models[model.id] = model
        for connection in definitions:
            for custom in connection.models:
                ref = model_reference(connection.id, custom.id)
                owners[ref] = connection
                models[ref] = ProviderModel(
                    id=ref,
                    label=custom.name,
                    litellm_model=f"openai/{custom.name}",
                    context_window=custom.context_window,
                    api_base=connection.base_url,
                    reasoning=CUSTOM_REASONING,
                    processing_profile="openai-attachments-v1",
                    capabilities=replace(
                        custom_attachment_capabilities(custom.images, custom.documents),
                        multimodal_tool_results=(
                            custom.images
                            and connection.protocol == "responses"
                            and urlsplit(connection.base_url).scheme == "https"
                            and urlsplit(connection.base_url).netloc == "api.openai.com"
                        ),
                    ),
                    responses=(
                        ResponsesRoute(custom.name, connection.base_url)
                        if connection.protocol == "responses"
                        else None
                    ),
                )
        self.models = MappingProxyType(models)
        self._owners = MappingProxyType(owners)

    def model(self, reference: str) -> ProviderModel:
        try:
            return self.models[reference]
        except KeyError:
            raise LLMModelUnavailableError(
                "所选模型或连接已不可用，请重新选择模型。", availability_code="model_not_configured"
            ) from None

    def default_config(self, reference: str) -> ModelRuntimeConfig:
        model = self.model(reference)
        return ModelRuntimeConfig(reference, model.reasoning.default_preset)

    def validate(self, config: ModelRuntimeConfig) -> ModelRuntimeConfig:
        if is_custom_model_reference(config.model_id):
            self.model(config.model_id)
            custom_effort(config.reasoning_preset)
            return config
        if self.model(config.model_id).reasoning.preset(config.reasoning_preset) is None:
            raise ValueError("The selected model does not support this reasoning preset")
        return config

    def credential_name(self, reference: str) -> str:
        self.model(reference)
        return self._owners[reference].credential_name

    def build_spec(self, config: ModelRuntimeConfig, *, api_key: str) -> ProviderSpec:
        self.validate(config)
        owner = self._owners[config.model_id]
        if isinstance(owner, ProviderDefinition):
            return build_provider_spec(
                provider_id=owner.provider_id, api_key=api_key, runtime_config=config
            )
        if not api_key.strip():
            raise LLMAuthError("自定义连接未配置 API Key。")
        model = self.model(config.model_id)
        return ProviderSpec(
            model=model.litellm_model,
            api_key=api_key.strip(),
            api_base=owner.base_url,
            timeout_is_default=True,
            defaults={
                "timeout": DEFAULT_LLM_TIMEOUT_SECONDS,
                **(
                    {"reasoning_effort": custom_effort(config.reasoning_preset)}
                    if custom_effort(config.reasoning_preset) is not None
                    else {}
                ),
            },
            context_window=model.context_window,
            context_window_source="connection",
            provider_id=f"custom/{owner.id}",
            product_model_id=model.id,
            reasoning_preset=config.reasoning_preset,
            attachment_capabilities=model.capabilities,
            attachment_profile=model.processing_profile,
            reasoning_replay_scope="conversation" if model.responses else "tool_turn",
            responses=model.responses,
        )


def custom_attachment_capabilities(images: bool, documents: bool) -> ModelRouteCapabilities:
    baseline = KIMI_MULTIMODAL_CAPABILITIES
    modalities: list[InputModality] = ["text"]
    if images:
        modalities.append("image")
    if documents:
        modalities.append("document")
    return replace(
        baseline,
        input_modalities=tuple(modalities),
        accepted_media_types=tuple(
            t
            for t in baseline.accepted_media_types
            if (images if t.startswith("image/") else documents)
        ),
    )
