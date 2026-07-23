from __future__ import annotations

import pytest
from llm_providers.spec import DEFAULT_LLM_TIMEOUT_SECONDS

from llm_providers import (
    LLMAuthError,
    build_provider_spec,
    default_model_for_provider,
    infer_provider_id_from_model,
    public_model_catalog,
    public_provider_models,
)


def test_deepseek_defaults_keep_existing_models_and_thinking_disabled() -> None:
    spec = build_provider_spec(provider_id="deepseek", api_key="k")

    assert spec.model == "deepseek/deepseek-v4-flash"
    assert spec.context_window == 1_000_000
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}
    assert spec.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS
    assert default_model_for_provider("deepseek", "memory") == "deepseek/deepseek-v4-pro"


def test_kimi_defaults_use_official_openai_compatible_base_url() -> None:
    spec = build_provider_spec(provider_id="kimi", api_key="k")

    assert spec.model == "openai/kimi-k2.6"
    assert spec.api_base == "https://api.moonshot.cn/v1"
    assert spec.defaults == {"timeout": DEFAULT_LLM_TIMEOUT_SECONDS}


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
    assert infer_provider_id_from_model("kimi/kimi-k2.7-code-highspeed") == "kimi"


def test_public_provider_models_are_safe_model_allowlists() -> None:
    models = public_provider_models()

    assert models["deepseek"] == (
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
    )
    assert "kimi/kimi-k2.6" in models["kimi"]


def test_public_model_catalog_is_the_capability_source_of_truth() -> None:
    catalog = public_model_catalog()
    providers = {provider["id"]: provider for provider in catalog["providers"]}
    deepseek = {model["id"]: model for model in providers["deepseek"]["models"]}
    kimi = {model["id"]: model for model in providers["kimi"]["models"]}

    assert catalog["schemaVersion"] == 1
    assert catalog["catalogRevision"]
    assert deepseek["deepseek/deepseek-v4-flash"]["capabilities"]["attachments"] is False
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
    assert kimi["kimi/kimi-k2.7-code"]["capabilities"]["attachments"] is False


def test_missing_provider_key_raises_actionable_auth_error() -> None:
    with pytest.raises(LLMAuthError, match="KIMI_API_KEY"):
        build_provider_spec(provider_id="kimi", api_key="")
