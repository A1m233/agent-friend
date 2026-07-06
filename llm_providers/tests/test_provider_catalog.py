from __future__ import annotations

import pytest

from llm_providers import (
    LLMAuthError,
    build_provider_spec,
    default_model_for_provider,
    infer_provider_id_from_model,
    public_provider_models,
)


def test_deepseek_defaults_keep_existing_models_and_thinking_disabled() -> None:
    spec = build_provider_spec(provider_id="deepseek", api_key="k")

    assert spec.model == "deepseek/deepseek-v4-flash"
    assert spec.context_window == 1_000_000
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}
    assert default_model_for_provider("deepseek", "memory") == "deepseek/deepseek-v4-pro"


def test_kimi_defaults_use_official_openai_compatible_base_url() -> None:
    spec = build_provider_spec(provider_id="kimi", api_key="k")

    assert spec.model == "openai/kimi-k2.6"
    assert spec.api_base == "https://api.moonshot.cn/v1"
    assert spec.defaults == {}


def test_kimi_model_id_maps_to_litellm_openai_compatible_model() -> None:
    spec = build_provider_spec(
        provider_id="kimi",
        api_key="k",
        model_id="kimi/kimi-k2.7-code-highspeed",
    )

    assert spec.model == "openai/kimi-k2.7-code-highspeed"
    assert infer_provider_id_from_model("kimi/kimi-k2.7-code-highspeed") == "kimi"


def test_public_provider_models_are_safe_model_allowlists() -> None:
    models = public_provider_models()

    assert models["deepseek"] == (
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
    )
    assert "kimi/kimi-k2.6" in models["kimi"]


def test_missing_provider_key_raises_actionable_auth_error() -> None:
    with pytest.raises(LLMAuthError, match="KIMI_API_KEY"):
        build_provider_spec(provider_id="kimi", api_key="")
