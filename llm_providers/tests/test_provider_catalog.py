from __future__ import annotations

import pytest
from llm_providers.spec import DEFAULT_LLM_TIMEOUT_SECONDS

from llm_providers import (
    DEEPSEEK_VISION_LITELLM_MODEL,
    DEEPSEEK_VISION_MODEL_ID,
    LLMAuthError,
    ModelRuntimeConfig,
    build_provider_spec,
    default_model_for_provider,
    default_model_runtime_config,
    infer_provider_id_from_model,
    public_model_catalog,
    public_provider_models,
    replace_spec_model,
    validate_model_runtime_config,
)


def test_deepseek_defaults_keep_existing_models_and_thinking_disabled() -> None:
    spec = build_provider_spec(provider_id="deepseek", api_key="k")

    assert spec.model == "openai/deepseek-flash"
    assert spec.context_window == 1_000_000
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}
    assert spec.reasoning_preset == "off"
    assert spec.reasoning_replay_scope == "none"
    assert spec.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS
    assert default_model_for_provider("deepseek", "memory") == "deepseek/deepseek-flash"


def test_deepseek_vision_uses_generic_openai_route_and_model_level_base() -> None:
    spec = build_provider_spec(
        provider_id="deepseek",
        api_key="k",
        model_id=DEEPSEEK_VISION_MODEL_ID,
    )

    assert spec.model == DEEPSEEK_VISION_LITELLM_MODEL
    assert spec.api_base == "https://api.deepseek.com"
    assert spec.context_window == 1_000_000
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}
    assert spec.reasoning_preset == "off"
    assert spec.reasoning_replay_scope == "none"
    assert infer_provider_id_from_model(DEEPSEEK_VISION_MODEL_ID) == "deepseek"

    rebound = replace_spec_model(
        build_provider_spec(provider_id="deepseek", api_key="k"), DEEPSEEK_VISION_MODEL_ID
    )
    assert rebound.model == DEEPSEEK_VISION_LITELLM_MODEL
    assert rebound.api_base == "https://api.deepseek.com"


def test_kimi_defaults_use_official_openai_compatible_base_url() -> None:
    spec = build_provider_spec(provider_id="kimi", api_key="k")

    assert spec.model == "openai/kimi-k2.6"
    assert spec.api_base == "https://api.moonshot.cn/v1"
    assert spec.context_window == 256 * 1024
    assert spec.context_window_source == "catalog"
    assert spec.defaults == {
        "extra_body": {"thinking": {"type": "enabled"}},
        "timeout": DEFAULT_LLM_TIMEOUT_SECONDS,
    }
    assert spec.reasoning_preset == "on"
    assert spec.reasoning_replay_scope == "tool_turn"


def test_provider_spec_allows_timeout_override() -> None:
    spec = build_provider_spec(provider_id="deepseek", api_key="k", timeout_seconds=12.5)

    assert spec.defaults["timeout"] == 12.5


def test_kimi_model_id_maps_to_litellm_openai_compatible_model() -> None:
    spec = build_provider_spec(
        provider_id="kimi",
        api_key="k",
        model_id="kimi/kimi-k2.7-code-highspeed",
    )

    assert spec.model == "openai/kimi-k2.7-code-highspeed"
    assert "extra_body" not in spec.defaults
    assert spec.reasoning_preset == "on"
    assert spec.reasoning_replay_scope == "conversation"
    assert infer_provider_id_from_model("kimi/kimi-k2.7-code-highspeed") == "kimi"


def test_openrouter_models_are_static_text_tool_routes_with_strict_defaults() -> None:
    spec = build_provider_spec(provider_id="openrouter", api_key="k")

    assert spec.model == "openrouter/google/gemini-3.7-flash"
    assert spec.context_window == 1_048_576
    assert spec.api_base is None
    assert spec.defaults == {
        "extra_body": {
            "provider": {
                "require_parameters": True,
                "data_collection": "deny",
            },
            "reasoning": {"effort": "medium"},
        },
        "timeout": DEFAULT_LLM_TIMEOUT_SECONDS,
    }
    assert default_model_for_provider("openrouter", "memory") == "openrouter/openai/gpt-5.6-luna"
    assert infer_provider_id_from_model("openrouter/openai/gpt-5.6-luna") == "openrouter"


def test_public_provider_models_are_safe_model_allowlists() -> None:
    models = public_provider_models()

    assert models["deepseek"] == (
        "deepseek/deepseek-v4-pro",
        DEEPSEEK_VISION_MODEL_ID,
    )
    assert "kimi/kimi-k2.6" in models["kimi"]


def test_public_model_catalog_is_the_capability_source_of_truth() -> None:
    catalog = public_model_catalog()
    providers = {provider["id"]: provider for provider in catalog["providers"]}
    deepseek = {model["id"]: model for model in providers["deepseek"]["models"]}
    kimi = {model["id"]: model for model in providers["kimi"]["models"]}
    assert "openrouter" not in providers  # 暂不维护，内部兼容由独立测试覆盖。

    assert catalog["schemaVersion"] == 2
    assert catalog["catalogRevision"]
    assert deepseek["deepseek/deepseek-flash"]["capabilities"]["attachments"] is True
    assert deepseek["deepseek/deepseek-flash"]["reasoning"] == {
        "control": "effort",
        "defaultPreset": "off",
        "presets": [
            {"id": "off", "label": "关闭"},
            {"id": "low", "label": "低"},
            {"id": "high", "label": "高"},
            {"id": "max", "label": "最高"},
        ],
    }
    vision = deepseek[DEEPSEEK_VISION_MODEL_ID]
    assert vision["label"] == "DeepSeek Flash"
    assert vision["processingProfile"] == "deepseek-vision-files-v1"
    assert vision["capabilities"]["inputModalities"] == ("text", "image")
    assert vision["capabilities"]["acceptedMediaTypes"] == (
        "image/jpeg",
        "image/png",
        "image/webp",
    )
    assert vision["capabilities"]["maxAttachmentBytes"] == 64 * 1024 * 1024
    assert vision["capabilities"]["maxTotalAttachmentBytes"] == 100 * 1024 * 1024
    assert vision["capabilities"]["maxAttachmentsPerTurn"] == 10
    assert vision["capabilities"]["multimodalToolResults"] is False
    assert vision["reasoning"] == {
        "control": "effort",
        "defaultPreset": "off",
        "presets": [
            {"id": "off", "label": "关闭"},
            {"id": "low", "label": "低"},
            {"id": "high", "label": "高"},
            {"id": "max", "label": "最高"},
        ],
    }
    assert kimi["kimi/kimi-k2.6"]["capabilities"]["inputModalities"] == (
        "text",
        "image",
        "document",
    )
    assert kimi["kimi/kimi-k2.6"]["capabilities"]["acceptedMediaTypes"] == (
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "text/plain",
    )
    assert kimi["kimi/kimi-k2.6"]["limits"] == {
        "contextWindow": 256 * 1024,
        "contextWindowSource": "catalog",
    }
    for provider in providers.values():
        for model in provider["models"]:
            assert model["limits"]["contextWindow"] > 0
    for model_id in ("kimi/kimi-k2.7-code", "kimi/kimi-k2.7-code-highspeed"):
        assert kimi[model_id]["processingProfile"] == "kimi-multimodal-v1"
        assert kimi[model_id]["capabilities"]["attachments"] is True
        assert kimi[model_id]["capabilities"]["inputModalities"] == (
            "text",
            "image",
            "document",
        )
        assert kimi[model_id]["capabilities"]["acceptedMediaTypes"] == (
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/pdf",
            "text/plain",
        )


def test_missing_provider_key_raises_actionable_auth_error() -> None:
    with pytest.raises(LLMAuthError, match="KIMI_API_KEY"):
        build_provider_spec(provider_id="kimi", api_key="")


def test_runtime_config_validation_and_defaults_are_model_specific() -> None:
    assert default_model_runtime_config("deepseek/deepseek-v4-pro") == ModelRuntimeConfig(
        "deepseek/deepseek-v4-pro", "off"
    )
    assert default_model_runtime_config(DEEPSEEK_VISION_MODEL_ID) == ModelRuntimeConfig(
        DEEPSEEK_VISION_MODEL_ID, "off"
    )
    assert default_model_runtime_config("kimi/kimi-k2.6") == ModelRuntimeConfig(
        "kimi/kimi-k2.6", "on"
    )
    assert default_model_runtime_config("openrouter/google/gemini-3.7-flash") == (
        ModelRuntimeConfig("openrouter/google/gemini-3.7-flash", "medium")
    )
    assert (
        validate_model_runtime_config(
            ModelRuntimeConfig("openrouter/openai/gpt-5.6-luna", "xhigh")
        ).reasoning_preset
        == "xhigh"
    )

    with pytest.raises(ValueError, match="不支持推理档位"):
        validate_model_runtime_config(ModelRuntimeConfig("kimi/kimi-k2.6", "high"))


def test_reasoning_presets_map_to_provider_options_without_losing_siblings() -> None:
    deepseek = build_provider_spec(
        provider_id="deepseek",
        api_key="k",
        runtime_config=ModelRuntimeConfig("deepseek/deepseek-v4-pro", "max"),
    )
    assert deepseek.defaults["reasoning_effort"] == "max"
    assert deepseek.defaults["extra_body"] == {"thinking": {"type": "enabled"}}
    assert deepseek.reasoning_replay_scope == "conversation"

    deepseek_vision = build_provider_spec(
        provider_id="deepseek",
        api_key="k",
        runtime_config=ModelRuntimeConfig(DEEPSEEK_VISION_MODEL_ID, "high"),
    )
    assert deepseek_vision.model == DEEPSEEK_VISION_LITELLM_MODEL
    assert deepseek_vision.defaults["reasoning_effort"] == "high"
    assert deepseek_vision.defaults["extra_body"] == {"thinking": {"type": "enabled"}}
    assert deepseek_vision.reasoning_replay_scope == "conversation"

    openrouter = build_provider_spec(
        provider_id="openrouter",
        api_key="k",
        model_id="openrouter/openai/gpt-5.6-luna",
        reasoning_preset="none",
    )
    assert openrouter.defaults["extra_body"] == {
        "provider": {"require_parameters": True, "data_collection": "deny"},
        "reasoning": {"effort": "none"},
    }
    assert openrouter.reasoning_replay_scope == "none"


def test_runtime_config_rejects_provider_model_mismatch() -> None:
    with pytest.raises(ValueError, match="不属于 provider"):
        build_provider_spec(
            provider_id="deepseek",
            api_key="k",
            runtime_config=ModelRuntimeConfig("kimi/kimi-k2.6", "on"),
        )


@pytest.mark.parametrize(
    "model",
    [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-reasoner",
    ],
)
def test_removed_models_remain_readable_but_have_no_executable_route(model: str) -> None:
    from llm_providers import LLMModelUnavailableError

    assert model not in public_provider_models()["deepseek"]
    config = default_model_runtime_config(model)
    assert config.model_id == model
    assert validate_model_runtime_config(config) == config
    with pytest.raises(LLMModelUnavailableError):
        build_provider_spec(provider_id="deepseek", api_key="fixture", runtime_config=config)
    with pytest.raises(LLMModelUnavailableError):
        build_provider_spec(provider_id="deepseek", api_key="fixture", model_id=model)
    with pytest.raises(LLMModelUnavailableError):
        build_provider_spec(
            provider_id="deepseek", api_key="fixture", model_id=model.split("/", 1)[1]
        )
