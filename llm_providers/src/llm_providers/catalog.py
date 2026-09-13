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

from .errors import LLMAuthError, LLMModelUnavailableError
from .model_identity import is_custom_model_reference
from .spec import DEFAULT_LLM_TIMEOUT_SECONDS, ProviderSpec, ResponsesRoute

ModelPurpose = Literal["chat", "memory"]
InputModality = Literal["text", "image", "document"]
ReasoningControl = Literal["unsupported", "toggle", "effort", "fixed"]
ReasoningReplayScope = Literal["none", "tool_turn", "conversation"]

CATALOG_REVISION = "2026-09-12.1"

DEEPSEEK_PROVIDER_ID = "deepseek"
KIMI_PROVIDER_ID = "kimi"
OPENROUTER_PROVIDER_ID = "openrouter"

DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
KIMI_API_KEY = "KIMI_API_KEY"
OPENROUTER_API_KEY = "OPENROUTER_API_KEY"

KIMI_API_BASE = "https://api.moonshot.cn/v1"
KIMI_CONTEXT_WINDOW = 256 * 1024
DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEEPSEEK_VISION_MODEL_ID = "deepseek/deepseek-flash"
DEEPSEEK_VISION_LITELLM_MODEL = "openai/deepseek-flash"


@dataclass(frozen=True)
class ModelRuntimeConfig:
    """One validated, indivisible chat-model runtime selection."""

    model_id: str
    reasoning_preset: str


@dataclass(frozen=True)
class ReasoningPresetDefinition:
    """One product-visible preset and its private provider call options."""

    id: str
    label: str
    call_options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelReasoningDefinition:
    """Reasoning controls and replay requirements for one concrete model."""

    control: ReasoningControl
    presets: tuple[ReasoningPresetDefinition, ...]
    default_preset: str
    replay_scope: ReasoningReplayScope = "none"

    def preset(self, preset_id: str) -> ReasoningPresetDefinition | None:
        return next((item for item in self.presets if item.id == preset_id), None)


UNSUPPORTED_REASONING = ModelReasoningDefinition(
    control="unsupported",
    presets=(ReasoningPresetDefinition("none", "不支持"),),
    default_preset="none",
)

CUSTOM_REASONING = ModelReasoningDefinition(
    control="effort",
    presets=(
        ReasoningPresetDefinition("none", "服务默认"),
        *(
            ReasoningPresetDefinition(f"effort:{value}", value)
            for value in ("none", "minimal", "low", "medium", "high", "xhigh", "max")
        ),
    ),
    default_preset="none",
    replay_scope="conversation",
)


DEEPSEEK_REASONING = ModelReasoningDefinition(
    control="effort",
    presets=(
        ReasoningPresetDefinition(
            "off",
            "关闭",
            {"extra_body": {"thinking": {"type": "disabled"}}},
        ),
        ReasoningPresetDefinition(
            "low",
            "低",
            {
                "reasoning_effort": "low",
                "extra_body": {"thinking": {"type": "enabled"}},
            },
        ),
        ReasoningPresetDefinition(
            "high",
            "高",
            {
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
            },
        ),
        ReasoningPresetDefinition(
            "max",
            "最高",
            {
                "reasoning_effort": "max",
                "extra_body": {"thinking": {"type": "enabled"}},
            },
        ),
    ),
    default_preset="off",
    replay_scope="conversation",
)

KIMI_TOGGLE_REASONING = ModelReasoningDefinition(
    control="toggle",
    presets=(
        ReasoningPresetDefinition(
            "off",
            "关闭",
            {"extra_body": {"thinking": {"type": "disabled"}}},
        ),
        ReasoningPresetDefinition(
            "on",
            "开启",
            {"extra_body": {"thinking": {"type": "enabled"}}},
        ),
    ),
    default_preset="on",
    replay_scope="tool_turn",
)

KIMI_FIXED_REASONING = ModelReasoningDefinition(
    control="fixed",
    presets=(ReasoningPresetDefinition("on", "固定开启"),),
    default_preset="on",
    replay_scope="conversation",
)

KIMI_K3_REASONING = ModelReasoningDefinition(
    control="effort",
    presets=tuple(
        ReasoningPresetDefinition(effort, label, {"reasoning_effort": effort})
        for effort, label in (("low", "低"), ("high", "高"), ("max", "最高"))
    ),
    default_preset="max",
    replay_scope="conversation",
)

OPENROUTER_GEMINI_REASONING = ModelReasoningDefinition(
    control="effort",
    presets=tuple(
        ReasoningPresetDefinition(
            preset_id,
            label,
            {"extra_body": {"reasoning": {"effort": preset_id}}},
        )
        for preset_id, label in (("low", "低"), ("medium", "中"), ("high", "高"))
    ),
    default_preset="medium",
    replay_scope="conversation",
)

OPENROUTER_LUNA_REASONING = ModelReasoningDefinition(
    control="effort",
    presets=tuple(
        ReasoningPresetDefinition(
            preset_id,
            label,
            {"extra_body": {"reasoning": {"effort": preset_id}}},
        )
        for preset_id, label in (
            ("none", "关闭"),
            ("low", "低"),
            ("medium", "中"),
            ("high", "高"),
            ("xhigh", "极高"),
            ("max", "最高"),
        )
    ),
    default_preset="medium",
    replay_scope="conversation",
)


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
DEEPSEEK_VISION_CAPABILITIES = ModelRouteCapabilities(
    multimodal_tool_results=True,
    input_modalities=("text", "image"),
    accepted_media_types=("image/jpeg", "image/png", "image/webp"),
    max_attachment_bytes=64 * 1024 * 1024,
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
    processing_profile: str = "text-only-v1"
    api_base: str | None = None
    capabilities: ModelRouteCapabilities = TEXT_ONLY_CAPABILITIES
    reasoning: ModelReasoningDefinition = UNSUPPORTED_REASONING
    responses: ResponsesRoute | None = None


@dataclass(frozen=True)
class ProviderDefinition:
    """Provider-specific defaults used to assemble ``ProviderSpec``."""

    provider_id: str
    display_name: str
    credential_name: str
    credential_group: str | None
    models: tuple[ProviderModel, ...]
    default_model: str
    default_memory_model: str
    api_base: str | None = None
    default_call_options: Mapping[str, Any] = field(default_factory=dict)
    publicly_visible: bool = True

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
                id="deepseek/deepseek-v4-pro",
                label="deepseek/deepseek-v4-pro",
                litellm_model="deepseek/deepseek-v4-pro",
                context_window=1_000_000,
                reasoning=DEEPSEEK_REASONING,
                responses=ResponsesRoute(
                    "deepseek-v4-pro", DEEPSEEK_API_BASE, "deepseek", "native"
                ),
            ),
            ProviderModel(
                id=DEEPSEEK_VISION_MODEL_ID,
                label="DeepSeek Flash",
                litellm_model=DEEPSEEK_VISION_LITELLM_MODEL,
                context_window=1_000_000,
                processing_profile="deepseek-vision-files-v1",
                api_base=DEEPSEEK_API_BASE,
                capabilities=DEEPSEEK_VISION_CAPABILITIES,
                reasoning=DEEPSEEK_REASONING,
                responses=ResponsesRoute("deepseek-flash", DEEPSEEK_API_BASE, "deepseek", "native"),
            ),
        ),
        default_model="deepseek/deepseek-flash",
        default_memory_model="deepseek/deepseek-flash",
        default_call_options={
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
                id="kimi/kimi-k3",
                label="kimi-k3",
                litellm_model="openai/kimi-k3",
                context_window=1_048_576,
                reasoning=KIMI_K3_REASONING,
                responses=ResponsesRoute("kimi-k3", KIMI_API_BASE, "kimi"),
            ),
            ProviderModel(
                id="kimi/kimi-k2.6",
                label="kimi-k2.6",
                litellm_model="openai/kimi-k2.6",
                context_window=KIMI_CONTEXT_WINDOW,
                processing_profile="kimi-multimodal-v1",
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
                reasoning=KIMI_TOGGLE_REASONING,
            ),
            ProviderModel(
                id="kimi/kimi-k2.5",
                label="kimi-k2.5",
                litellm_model="openai/kimi-k2.5",
                context_window=KIMI_CONTEXT_WINDOW,
                processing_profile="kimi-multimodal-v1",
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
                reasoning=KIMI_TOGGLE_REASONING,
            ),
            ProviderModel(
                id="kimi/kimi-k2.7-code",
                label="kimi-k2.7-code",
                litellm_model="openai/kimi-k2.7-code",
                context_window=KIMI_CONTEXT_WINDOW,
                processing_profile="kimi-multimodal-v1",
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
                reasoning=KIMI_FIXED_REASONING,
            ),
            ProviderModel(
                id="kimi/kimi-k2.7-code-highspeed",
                label="kimi-k2.7-code-highspeed",
                litellm_model="openai/kimi-k2.7-code-highspeed",
                context_window=KIMI_CONTEXT_WINDOW,
                processing_profile="kimi-multimodal-v1",
                capabilities=KIMI_MULTIMODAL_CAPABILITIES,
                reasoning=KIMI_FIXED_REASONING,
            ),
        ),
        default_model="kimi/kimi-k2.6",
        default_memory_model="kimi/kimi-k2.6",
        api_base=KIMI_API_BASE,
        default_call_options={"timeout": DEFAULT_LLM_TIMEOUT_SECONDS},
    ),
    OPENROUTER_PROVIDER_ID: ProviderDefinition(
        # Temporarily unmaintained: hide public entry points, retain legacy
        # configuration and Chat transport compatibility. No Responses rollout.
        provider_id=OPENROUTER_PROVIDER_ID,
        publicly_visible=False,
        display_name="OpenRouter",
        credential_name=OPENROUTER_API_KEY,
        credential_group=OPENROUTER_PROVIDER_ID,
        models=(
            ProviderModel(
                id="openrouter/google/gemini-3.7-flash",
                label="Google · Gemini 3.7 Flash",
                litellm_model="openrouter/google/gemini-3.7-flash",
                context_window=1_048_576,
                reasoning=OPENROUTER_GEMINI_REASONING,
            ),
            ProviderModel(
                id="openrouter/openai/gpt-5.6-luna",
                label="OpenAI · GPT-5.6 Luna",
                litellm_model="openrouter/openai/gpt-5.6-luna",
                context_window=1_050_000,
                reasoning=OPENROUTER_LUNA_REASONING,
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
    if provider_id == "custom":
        return is_custom_model_reference(model_id)
    provider = provider_definition(provider_id)
    return provider.model(model_id) is not None


def model_capabilities(model_id: str) -> ModelRouteCapabilities:
    if is_custom_model_reference(model_id):
        return TEXT_ONLY_CAPABILITIES
    provider = provider_definition(infer_provider_id_from_model(model_id))
    model = provider.model(model_id)
    return model.capabilities if model is not None else TEXT_ONLY_CAPABILITIES


REMOVED_DEEPSEEK_MODELS = frozenset(
    {
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-reasoner",
    }
)


def require_model_not_removed(model_id: str) -> None:
    """Reject retired built-in routes before provider-side aliasing can occur."""
    if model_id in REMOVED_DEEPSEEK_MODELS:
        raise LLMModelUnavailableError(
            "所选模型已不可用，请重新选择模型。", availability_code="model_not_configured"
        )


def model_reasoning(model_id: str) -> ModelReasoningDefinition:
    # Historical snapshots retain their model identity and reasoning settings.
    # This is read compatibility, not an alias or permission to execute.
    if model_id in REMOVED_DEEPSEEK_MODELS:
        return (
            dataclasses.replace(DEEPSEEK_REASONING, default_preset="high")
            if model_id.endswith("/deepseek-reasoner")
            else DEEPSEEK_REASONING
        )
    if is_custom_model_reference(model_id):
        return CUSTOM_REASONING
    provider = provider_definition(infer_provider_id_from_model(model_id))
    model = provider.model(model_id)
    if model is None:
        raise ValueError(f"不支持的 model: {model_id!r}")
    return model.reasoning


def default_model_runtime_config(model_id: str) -> ModelRuntimeConfig:
    reasoning = model_reasoning(model_id)
    return ModelRuntimeConfig(model_id=model_id, reasoning_preset=reasoning.default_preset)


def minimum_model_runtime_config(model_id: str) -> ModelRuntimeConfig:
    """Select the least expensive supported reasoning preset from this catalog."""
    reasoning = model_reasoning(model_id)
    for preset in ("off", "none", "minimal", "low", "medium", "high", "xhigh", "max", "on"):
        if reasoning.preset(preset) is not None:
            return ModelRuntimeConfig(model_id, preset)
    raise ValueError(f"Model {model_id!r} has no supported minimum reasoning preset")


def validate_model_runtime_config(config: ModelRuntimeConfig) -> ModelRuntimeConfig:
    if is_custom_model_reference(config.model_id):
        from .custom_reasoning import custom_effort

        custom_effort(config.reasoning_preset)
        return config
    reasoning = model_reasoning(config.model_id)
    if reasoning.preset(config.reasoning_preset) is None:
        raise ValueError(f"模型 {config.model_id!r} 不支持推理档位 {config.reasoning_preset!r}")
    return config


def model_processing_profile(model_id: str) -> str:
    if is_custom_model_reference(model_id):
        return "text-only-v1"
    provider = provider_definition(infer_provider_id_from_model(model_id))
    model = provider.model(model_id)
    return model.processing_profile if model is not None else "text-only-v1"


def _fallback_litellm_model(provider: ProviderDefinition, model_id: str) -> str:
    if provider.provider_id == KIMI_PROVIDER_ID and model_id.startswith("kimi/"):
        return "openai/" + model_id.removeprefix("kimi/")
    return model_id


def _merge_mappings(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge provider options without discarding sibling policies."""

    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mappings(current, value)
        else:
            merged[key] = value
    return merged


def build_provider_spec(
    *,
    provider_id: str,
    api_key: str,
    model_id: str | None = None,
    reasoning_preset: str | None = None,
    runtime_config: ModelRuntimeConfig | None = None,
    purpose: ModelPurpose = "chat",
    context_window: int | None = None,
    timeout_seconds: float | None = None,
) -> ProviderSpec:
    """Build a ``ProviderSpec`` from a provider selection and API key."""

    provider = provider_definition(provider_id)
    requested_model = runtime_config.model_id if runtime_config else model_id
    if requested_model:
        require_model_not_removed(requested_model)
        if provider.provider_id == DEEPSEEK_PROVIDER_ID and "/" not in requested_model:
            require_model_not_removed("deepseek/" + requested_model)
    key = api_key.strip()
    if not key:
        raise LLMAuthError(f"{provider.credential_name} 未配置。请先配置 provider credential。")

    selected_config: ModelRuntimeConfig | None
    if runtime_config is not None:
        if model_id is not None and model_id != runtime_config.model_id:
            raise ValueError("model_id 与 runtime_config.model_id 不一致")
        if reasoning_preset is not None and reasoning_preset != runtime_config.reasoning_preset:
            raise ValueError("reasoning_preset 与 runtime_config.reasoning_preset 不一致")
        selected_config = validate_model_runtime_config(runtime_config)
        selected_model = selected_config.model_id
    else:
        selected_model = model_id or default_model_for_provider(provider.provider_id, purpose)
        selected_definition = provider.model(selected_model)
        selected_config = (
            validate_model_runtime_config(
                ModelRuntimeConfig(
                    model_id=selected_model,
                    reasoning_preset=(
                        reasoning_preset or selected_definition.reasoning.default_preset
                    ),
                )
            )
            if selected_definition is not None
            else None
        )
        if selected_definition is None and reasoning_preset is not None:
            raise ValueError("目录外 model 不能指定产品 reasoning preset")
    model = provider.model(selected_model)
    if model is None and selected_model in all_model_ids():
        raise ValueError(f"模型 {selected_model!r} 不属于 provider {provider.provider_id!r}")
    litellm_model = (
        model.litellm_model if model else _fallback_litellm_model(provider, selected_model)
    )
    model_window = model.context_window if model else None

    defaults = dict(provider.default_call_options)
    replay_scope: ReasoningReplayScope = "none"
    effective_preset: str | None = None
    if model is not None and selected_config is not None:
        preset = model.reasoning.preset(selected_config.reasoning_preset)
        assert preset is not None
        defaults = _merge_mappings(defaults, preset.call_options)
        effective_preset = preset.id
        if preset.id not in {"off", "none"}:
            replay_scope = model.reasoning.replay_scope
    if timeout_seconds is not None:
        defaults["timeout"] = timeout_seconds

    return ProviderSpec(
        model=litellm_model,
        api_key=key,
        api_base=model.api_base if model and model.api_base is not None else provider.api_base,
        defaults=defaults,
        context_window=context_window or model_window,
        context_window_source=(
            "runtime_override" if context_window is not None else "catalog" if model else None
        ),
        provider_id=provider.provider_id,
        product_model_id=selected_model,
        reasoning_preset=effective_preset,
        reasoning_replay_scope=replay_scope,
        responses=model.responses if model else None,
    )


def public_provider_models() -> dict[str, tuple[str, ...]]:
    """Return safe model ids suitable for allowlists and UI mirroring."""

    return {
        provider_id: tuple(model.id for model in provider.models)
        for provider_id, provider in PROVIDER_DEFINITIONS.items()
        if provider.publicly_visible
    }


def public_model_catalog(custom_models: tuple[ProviderModel, ...] = ()) -> dict[str, Any]:
    """Return the credential-free model route catalog for public clients."""

    providers: list[dict[str, Any]] = []
    definitions = list(PROVIDER_DEFINITIONS.values())
    if custom_models:
        definitions.append(
            ProviderDefinition(
                provider_id="custom",
                display_name="自定义连接",
                credential_name="",
                credential_group=None,
                models=custom_models,
                default_model=custom_models[0].id,
                default_memory_model=custom_models[0].id,
            )
        )
    for provider in definitions:
        if not provider.publicly_visible:
            continue
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
                    "processingProfile": model.processing_profile,
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
                    },
                    "reasoning": {
                        "control": model.reasoning.control,
                        **({"allowCustom": True} if is_custom_model_reference(model.id) else {}),
                        "defaultPreset": model.reasoning.default_preset,
                        "presets": [
                            {"id": preset.id, "label": preset.label}
                            for preset in model.reasoning.presets
                        ],
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
    return {"schemaVersion": 2, "catalogRevision": CATALOG_REVISION, "providers": providers}


def replace_spec_model(
    spec: ProviderSpec,
    model_id: str,
    reasoning_preset: str | None = None,
) -> ProviderSpec:
    """Return ``spec`` with model/api_base/defaults adjusted for ``model_id``.

    This helper keeps compatibility with places that have already resolved the
    provider credentials but need to bind a session-specific model.
    """

    provider = provider_definition(infer_provider_id_from_model(model_id))
    timeout = spec.defaults.get("timeout")
    rebuilt = build_provider_spec(
        provider_id=provider.provider_id,
        api_key=spec.api_key,
        model_id=model_id,
        reasoning_preset=reasoning_preset,
        timeout_seconds=float(timeout) if isinstance(timeout, (int, float)) else None,
    )
    return dataclasses.replace(
        rebuilt,
        context_window=(
            rebuilt.context_window if rebuilt.context_window is not None else spec.context_window
        ),
        context_window_source=(
            rebuilt.context_window_source
            if rebuilt.context_window is not None
            else spec.context_window_source
        ),
    )
