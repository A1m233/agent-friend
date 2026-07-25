"""009 M1：``LLMClient.context_window`` 三层兜底 + ``_extract_usage`` 单测。"""

from __future__ import annotations

import logging
import ssl
from types import SimpleNamespace
from typing import Any

import pytest
from litellm.exceptions import ContentPolicyViolationError as LiteLLMContentPolicyViolationError
from litellm.exceptions import Timeout as LiteLLMTimeout
from llm_providers.client import DEFAULT_CONTEXT_WINDOW, _extract_usage, _map_exception
from llm_providers.spec import DEFAULT_LLM_TIMEOUT_SECONDS

from llm_providers import (
    LLMClient,
    LLMContentPolicyError,
    LLMNetworkError,
    LLMProviderError,
    LLMRetryScheduled,
    LLMTextDelta,
    LLMTransientProviderError,
    LLMUsage,
    ProviderConfig,
    ProviderSpec,
)

_GET_MODEL_INFO = "llm_providers.client.litellm.get_model_info"
_COMPLETION = "llm_providers.client.litellm.completion"


def _spec(**kw: object) -> ProviderSpec:
    base: dict[str, object] = {"model": "deepseek/deepseek-chat", "api_key": "x"}
    base.update(kw)
    return ProviderSpec(**base)  # type: ignore[arg-type]


def test_deepseek_content_risk_maps_to_content_policy_error() -> None:
    mapped = _map_exception(RuntimeError("DeepseekException - Content Exists Risk"))

    assert isinstance(mapped, LLMContentPolicyError)


def test_litellm_typed_content_policy_error_maps_without_string_matching() -> None:
    provider_error = LiteLLMContentPolicyViolationError(
        message="provider rejected content",
        model="test-model",
        llm_provider="test-provider",
    )

    assert isinstance(_map_exception(provider_error), LLMContentPolicyError)


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
    assert seen_kwargs["max_retries"] == 0


def test_stream_passes_spec_timeout_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_kwargs: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        seen_kwargs.update(kwargs)
        return iter(())

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(defaults={"timeout": 12.5}))

    list(client.stream([{"role": "user", "content": "hi"}]))

    assert seen_kwargs["timeout"] == 12.5
    assert seen_kwargs["max_retries"] == 0


def test_stream_clamps_provider_timeout_to_bounded_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_kwargs: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        seen_kwargs.update(kwargs)
        return iter(())

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(
        _spec(defaults={"timeout": 90.0}),
        stream_timeout_seconds=45.0,
    )

    list(client.stream([{"role": "user", "content": "hi"}]))

    assert seen_kwargs["timeout"] == 45.0


def test_stream_close_closes_upstream_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class ClosableResponse:
        closed = False

        def __iter__(self) -> ClosableResponse:
            return self

        def __next__(self) -> Any:
            return SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="一", tool_calls=[]),
                        finish_reason=None,
                    )
                ],
            )

        def close(self) -> None:
            self.closed = True

    response = ClosableResponse()
    monkeypatch.setattr(_COMPLETION, lambda **_kwargs: response)
    client = LLMClient(_spec())

    stream: Any = client.stream([{"role": "user", "content": "hi"}])
    assert next(stream).text == "一"
    close = stream.close
    close()

    assert response.closed is True


def test_litellm_timeout_maps_to_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_completion(**_kwargs: Any) -> Any:
        raise LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek")

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(defaults={"timeout": 0.01}), network_retries=0)

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
    client = LLMClient(_spec(defaults={"timeout": 0.01}), network_retries=0)

    with pytest.raises(LLMNetworkError):
        list(client.stream([{"role": "user", "content": "hi"}]))


def test_wrapped_ssl_error_maps_to_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_completion(**_kwargs: Any) -> Any:
        try:
            raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")
        except ssl.SSLError as cause:
            raise RuntimeError("provider wrapped TLS failure") from cause

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=0)

    with pytest.raises(LLMNetworkError):
        list(client.stream([{"role": "user", "content": "hi"}]))


def test_complete_retries_network_error_before_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_completion(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=1, network_retry_backoff_seconds=0)

    assert client.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert calls == 2


def test_stream_retries_network_error_before_first_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_completion(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek")
        return iter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="ok", tool_calls=[]),
                            finish_reason="stop",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=1, network_retry_backoff_seconds=0)

    events = list(client.stream([{"role": "user", "content": "hi"}]))

    assert calls == 2
    retry = next(event for event in events if isinstance(event, LLMRetryScheduled))
    assert retry.attempt == 2
    assert retry.max_attempts == 2
    assert retry.delay_seconds == 0
    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["ok"]


def test_stream_does_not_retry_after_first_event(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class PartialThenTimeout:
        def __init__(self) -> None:
            self._step = 0

        def __iter__(self) -> PartialThenTimeout:
            return self

        def __next__(self) -> Any:
            self._step += 1
            if self._step == 1:
                return SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="partial", tool_calls=[]),
                            finish_reason=None,
                        )
                    ],
                )
            raise LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek")

    def fake_completion(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return PartialThenTimeout()

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=2, network_retry_backoff_seconds=0)
    stream = client.stream([{"role": "user", "content": "hi"}])

    first = next(stream)
    assert isinstance(first, LLMTextDelta)
    assert first.text == "partial"
    with pytest.raises(LLMNetworkError):
        next(stream)
    assert calls == 1


class _StatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider returned HTTP {status_code}")
        self.status_code = status_code


def test_stream_retries_provider_503_before_first_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_completion(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _StatusError(503)
        return iter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="ok", tool_calls=[]),
                            finish_reason="stop",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=2, network_retry_backoff_seconds=0)

    events = list(client.stream([{"role": "user", "content": "hi"}]))

    assert calls == 3
    assert len([event for event in events if isinstance(event, LLMRetryScheduled)]) == 2
    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["ok"]


def test_provider_503_maps_to_transient_provider_error() -> None:
    assert isinstance(_map_exception(_StatusError(503)), LLMTransientProviderError)


def test_stream_does_not_retry_permanent_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_completion(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise _StatusError(400)

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=2, network_retry_backoff_seconds=0)

    with pytest.raises(LLMProviderError):
        list(client.stream([{"role": "user", "content": "hi"}]))
    assert calls == 1


def test_stream_logs_privacy_safe_empty_response_shape(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_reasoning = "private reasoning must not be logged"

    def fake_completion(**_kwargs: Any) -> Any:
        return iter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content=secret_reasoning,
                                tool_calls=[],
                            ),
                            finish_reason="stop",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(_COMPLETION, fake_completion)
    caplog.set_level(logging.INFO, logger="llm_providers.client")
    client = LLMClient(_spec(), network_retries=0)

    list(client.stream([{"role": "user", "content": "sensitive prompt"}]))

    diagnostic = next(
        record.getMessage()
        for record in caplog.records
        if "[EmptyResponseDbg]" in record.getMessage()
    )
    assert "text_deltas=0" in diagnostic
    assert "tool_call_deltas=0" in diagnostic
    assert "reasoning_deltas=1" in diagnostic
    assert "reasoning_content" in diagnostic
    assert "finish_reason_raw=stop" in diagnostic
    assert secret_reasoning not in diagnostic
    assert "sensitive prompt" not in diagnostic
