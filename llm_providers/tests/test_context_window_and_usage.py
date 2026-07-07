"""009 M1：``LLMClient.context_window`` 三层兜底 + ``_extract_usage`` 单测。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from litellm.exceptions import Timeout as LiteLLMTimeout
from llm_providers.client import DEFAULT_CONTEXT_WINDOW, _extract_usage
from llm_providers.spec import DEFAULT_LLM_TIMEOUT_SECONDS

from llm_providers import LLMClient, LLMNetworkError, LLMUsage, ProviderConfig, ProviderSpec

_GET_MODEL_INFO = "llm_providers.client.litellm.get_model_info"
_COMPLETION = "llm_providers.client.litellm.completion"


def _spec(**kw: object) -> ProviderSpec:
    base: dict[str, object] = {"model": "deepseek/deepseek-chat", "api_key": "x"}
    base.update(kw)
    return ProviderSpec(**base)  # type: ignore[arg-type]


# ===== context_window 三层兜底 =====


def test_context_window_uses_spec_override_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 1 层：spec.context_window override 优先于 litellm 查询。"""

    def _boom(_model: str) -> dict[str, int]:  # litellm 不应被调用
        raise AssertionError("override 命中时不应查 litellm")

    monkeypatch.setattr(_GET_MODEL_INFO, _boom)
    client = LLMClient(_spec(context_window=4096))
    assert client.context_window == 4096


def test_context_window_falls_back_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 2 层：无 override 时取 litellm 元数据 max_input_tokens。"""
    monkeypatch.setattr(_GET_MODEL_INFO, lambda _model: {"max_input_tokens": 200000})
    client = LLMClient(_spec())
    assert client.context_window == 200000


def test_context_window_litellm_max_tokens_fallback_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """litellm 只给 max_tokens（无 max_input_tokens）时也能取到。"""
    monkeypatch.setattr(_GET_MODEL_INFO, lambda _model: {"max_tokens": 32000})
    client = LLMClient(_spec())
    assert client.context_window == 32000


def test_context_window_default_when_litellm_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 3 层：litellm 抛错（不认识私有 model）→ 保守默认窗口。"""

    def _raise(_model: str) -> dict[str, int]:
        raise Exception("unknown model")

    monkeypatch.setattr(_GET_MODEL_INFO, _raise)
    client = LLMClient(_spec())
    assert client.context_window == DEFAULT_CONTEXT_WINDOW


def test_context_window_default_when_litellm_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """litellm 返回无窗口字段 → 保守默认窗口。"""
    monkeypatch.setattr(_GET_MODEL_INFO, lambda _model: {})
    client = LLMClient(_spec())
    assert client.context_window == DEFAULT_CONTEXT_WINDOW


def test_spec_from_env_reads_context_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env 读 {prefix}_CONTEXT_WINDOW；非法值忽略回落 None。"""
    monkeypatch.setenv("ACME_API_KEY", "k")
    monkeypatch.setenv("ACME_MODEL", "acme/m")
    monkeypatch.setenv("ACME_CONTEXT_WINDOW", "65536")
    spec = ProviderSpec.from_env("ACME")
    assert spec.context_window == 65536

    monkeypatch.setenv("ACME_CONTEXT_WINDOW", "not-a-number")
    spec2 = ProviderSpec.from_env("ACME")
    assert spec2.context_window is None


def test_spec_from_env_reads_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env 读 {prefix}_TIMEOUT_SECONDS；非法值回落 provider 默认。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "12.5")

    spec = ProviderSpec.from_env("DEEPSEEK")

    assert spec.defaults["timeout"] == 12.5

    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "not-a-number")
    spec2 = ProviderSpec.from_env("DEEPSEEK")
    assert spec2.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS


def test_spec_from_env_can_read_dedicated_model_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旁路任务可用专门的 model env，不影响主 ``{prefix}_MODEL``。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_MEMORY_MODEL", "deepseek/deepseek-v4-pro")

    spec = ProviderSpec.from_env(
        "DEEPSEEK",
        model_env_var="DEEPSEEK_MEMORY_MODEL",
        default_model_key="memory_model",
    )

    assert spec.model == "deepseek/deepseek-v4-pro"


def test_spec_from_env_memory_model_defaults_to_v4_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEPSEEK_MEMORY_MODEL 未设置时，记忆抽取默认走 V4 Pro。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.delenv("DEEPSEEK_MEMORY_MODEL", raising=False)

    spec = ProviderSpec.from_env(
        "DEEPSEEK",
        model_env_var="DEEPSEEK_MEMORY_MODEL",
        default_model_key="memory_model",
    )

    assert spec.model == "deepseek/deepseek-v4-pro"


def test_spec_from_config_uses_explicit_config_and_defaults() -> None:
    spec = ProviderSpec.from_config(
        "DEEPSEEK",
        ProviderConfig(api_key="k", context_window=1000000),
    )

    assert spec.api_key == "k"
    assert spec.model == "deepseek/deepseek-v4-flash"
    assert spec.context_window == 1000000
    assert spec.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS


def test_spec_from_config_allows_timeout_override() -> None:
    spec = ProviderSpec.from_config(
        "DEEPSEEK",
        ProviderConfig(api_key="k", timeout_seconds=15.0),
    )

    assert spec.defaults["timeout"] == 15.0


def test_spec_from_config_memory_model_defaults_to_v4_pro() -> None:
    spec = ProviderSpec.from_config(
        "DEEPSEEK",
        ProviderConfig(api_key="k"),
        default_model_key="memory_model",
    )

    assert spec.model == "deepseek/deepseek-v4-pro"


# ===== _extract_usage =====


def test_extract_usage_from_response() -> None:
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150)
    )
    usage = _extract_usage(resp)
    assert usage == LLMUsage(prompt_tokens=120, completion_tokens=30, total_tokens=150)


def test_extract_usage_none_when_missing() -> None:
    assert _extract_usage(SimpleNamespace(usage=None)) is None
    assert _extract_usage(SimpleNamespace()) is None


def test_extract_usage_handles_partial_fields() -> None:
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=99))
    usage = _extract_usage(resp)
    assert usage is not None
    assert usage.prompt_tokens == 99
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0


# ===== timeout 透传与异常映射 =====


def test_complete_passes_spec_timeout_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_kwargs: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        seen_kwargs.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(defaults={"timeout": 12.5}))

    assert client.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert seen_kwargs["timeout"] == 12.5


def test_stream_passes_spec_timeout_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_kwargs: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        seen_kwargs.update(kwargs)
        return iter(())

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(defaults={"timeout": 12.5}))

    list(client.stream([{"role": "user", "content": "hi"}]))

    assert seen_kwargs["timeout"] == 12.5


def test_litellm_timeout_maps_to_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_completion(**_kwargs: Any) -> Any:
        raise LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek")

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(defaults={"timeout": 0.01}))

    with pytest.raises(LLMNetworkError):
        list(client.stream([{"role": "user", "content": "hi"}]))


def test_litellm_stream_iteration_timeout_maps_to_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutIterator:
        def __iter__(self) -> TimeoutIterator:
            return self

        def __next__(self) -> Any:
            raise LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek")

    monkeypatch.setattr(_COMPLETION, lambda **_kwargs: TimeoutIterator())
    client = LLMClient(_spec(defaults={"timeout": 0.01}))

    with pytest.raises(LLMNetworkError):
        list(client.stream([{"role": "user", "content": "hi"}]))
