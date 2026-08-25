from __future__ import annotations

import pytest
from agent_eval.__main__ import _provider_spec

from runtime_config import CredentialMissingError, EnvSource


def test_provider_spec_uses_kimi_credential_for_kimi_model() -> None:
    spec = _provider_spec(
        "kimi/kimi-k2.6",
        EnvSource({"KIMI_API_KEY": "kimi-test-key"}),
    )

    assert spec.model == "openai/kimi-k2.6"
    assert spec.api_key == "kimi-test-key"


def test_provider_spec_does_not_fall_back_to_deepseek_key_for_kimi() -> None:
    with pytest.raises(CredentialMissingError, match="KIMI_API_KEY"):
        _provider_spec(
            "kimi/kimi-k2.6",
            EnvSource({"DEEPSEEK_API_KEY": "deepseek-test-key"}),
        )


def test_provider_spec_preserves_provider_api_base_override() -> None:
    spec = _provider_spec(
        "deepseek/custom-model",
        EnvSource(
            {
                "DEEPSEEK_API_KEY": "deepseek-test-key",
                "DEEPSEEK_API_BASE": "https://gateway.example/v1",
            }
        ),
    )

    assert spec.api_base == "https://gateway.example/v1"


def test_provider_spec_applies_deepseek_max_reasoning_preset() -> None:
    spec = _provider_spec(
        "deepseek/deepseek-v4-flash-vision-exp",
        EnvSource({"DEEPSEEK_API_KEY": "deepseek-test-key"}),
        "max",
    )

    assert spec.product_model_id == "deepseek/deepseek-v4-flash-vision-exp"
    assert spec.reasoning_preset == "max"
    assert spec.defaults["reasoning_effort"] == "max"
    assert spec.defaults["extra_body"] == {"thinking": {"type": "enabled"}}


def test_provider_spec_rejects_unsupported_reasoning_preset() -> None:
    with pytest.raises(ValueError, match="不支持推理档位"):
        _provider_spec(
            "deepseek/deepseek-v4-flash-vision-exp",
            EnvSource({"DEEPSEEK_API_KEY": "deepseek-test-key"}),
            "xhigh",
        )
