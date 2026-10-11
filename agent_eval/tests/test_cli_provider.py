from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_eval.__main__ import _provider_spec

from runtime_config import CredentialMissingError, EnvSource


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_custom_provider_uses_product_directory_and_own_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, protocol: str
) -> None:
    from llm_providers.errors import LLMModelUnavailableError

    import runtime_config

    connection = "00000000-0000-4000-8000-000000000101"
    model = "00000000-0000-4000-8000-000000000102"
    reference = f"custom/{connection}/{model}"
    settings = {
        "modelConnections": {
            "revision": 1,
            "connections": [
                {
                    "id": connection,
                    "name": "fixture",
                    "baseUrl": "https://fixture.example/v1",
                    "protocol": protocol,
                    "credentialRevision": connection,
                    "models": [{"id": model, "name": "fixture-model", "contextWindow": 32000}],
                }
            ],
        }
    }
    reader = SimpleNamespace(path=tmp_path / "settings.json", read=lambda: settings)
    monkeypatch.setattr(runtime_config, "DesktopSettingsReader", lambda: reader)
    source = EnvSource({f"CUSTOM_LLM_{connection}_{connection}_API_KEY": "fixture-key"})
    spec = _provider_spec(reference, source, "effort:medium")
    assert spec.product_model_id == reference
    assert spec.model == "openai/fixture-model"
    assert spec.defaults["reasoning_effort"] == "medium"
    assert spec.api_key == "fixture-key"
    assert spec.context_window == 32000
    assert (spec.responses is not None) == (protocol == "responses")
    assert _provider_spec(reference, source).reasoning_preset == "none"
    with pytest.raises(CredentialMissingError):
        _provider_spec(reference, EnvSource({"DEEPSEEK_API_KEY": "wrong-key"}))
    with pytest.raises(LLMModelUnavailableError):
        _provider_spec(reference.replace(model, connection), source)
    reader.path.write_text("invalid", encoding="utf-8")
    reader.read = lambda: None
    with pytest.raises(ValueError, match="Model settings cannot be read"):
        _provider_spec(reference, source)


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
        "deepseek/deepseek-flash",
        EnvSource({"DEEPSEEK_API_KEY": "deepseek-test-key"}),
        "max",
    )

    assert spec.product_model_id == "deepseek/deepseek-flash"
    assert spec.reasoning_preset == "max"
    assert spec.defaults["reasoning_effort"] == "max"
    assert spec.defaults["extra_body"] == {"thinking": {"type": "enabled"}}


def test_provider_spec_rejects_unsupported_reasoning_preset() -> None:
    with pytest.raises(ValueError, match="不支持推理档位"):
        _provider_spec(
            "deepseek/deepseek-flash",
            EnvSource({"DEEPSEEK_API_KEY": "deepseek-test-key"}),
            "xhigh",
        )
