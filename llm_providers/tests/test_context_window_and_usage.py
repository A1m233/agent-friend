"""009 M1：``LLMClient.context_window`` 三层兜底 + ``_extract_usage`` 单测。"""

from __future__ import annotations

import asyncio
import logging
import ssl
import threading
import time
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from litellm.exceptions import ContentPolicyViolationError as LiteLLMContentPolicyViolationError
from litellm.exceptions import ContextWindowExceededError as LiteLLMContextWindowExceededError
from litellm.exceptions import Timeout as LiteLLMTimeout
from llm_providers.client import _extract_stream_usage, _extract_usage, _map_exception
from llm_providers.spec import DEFAULT_LLM_TIMEOUT_SECONDS
from openai.types.chat import ChatCompletionChunk

from llm_providers import (
    LLMActivity,
    LLMAuthError,
    LLMClient,
    LLMContentPolicyError,
    LLMContextWindowError,
    LLMEmptyResponseError,
    LLMModelMetadataError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRetryScheduled,
    LLMStreamInterruptedAfterOutputError,
    LLMStreamTimeoutError,
    LLMTextDelta,
    LLMTransientProviderError,
    LLMTurnDone,
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


def test_litellm_context_window_error_maps_to_specific_error() -> None:
    provider_error = LiteLLMContextWindowExceededError(
        message="request is too large",
        model="test-model",
        llm_provider="test-provider",
    )

    assert isinstance(_map_exception(provider_error), LLMContextWindowError)


def test_wrapped_context_window_code_maps_to_specific_error() -> None:
    class ProviderBadRequest(Exception):
        code = "context_length_exceeded"

    assert isinstance(_map_exception(ProviderBadRequest("too large")), LLMContextWindowError)


# ===== context_window 三层兜底 =====


def test_context_window_uses_spec_override_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 1 层：spec.context_window override 优先于 litellm 查询。"""

    def _boom(_model: str) -> dict[str, int]:  # litellm 不应被调用
        raise AssertionError("override 命中时不应查 litellm")

    monkeypatch.setattr(_GET_MODEL_INFO, _boom)
    client = LLMClient(_spec(context_window=4096))
    assert client.context_window == 4096
    assert client.model_limits.context_window_source == "provider_spec"


def test_context_window_falls_back_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 2 层：无 override 时取 litellm 元数据 max_input_tokens。"""
    monkeypatch.setattr(_GET_MODEL_INFO, lambda _model: {"max_input_tokens": 200000})
    client = LLMClient(_spec())
    assert client.context_window == 200000
    assert client.model_limits.context_window_source == "litellm.max_input_tokens"


def test_context_window_rejects_output_limit_as_input_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_tokens 是输出上限，不能被误用成输入上下文窗口。"""
    monkeypatch.setattr(_GET_MODEL_INFO, lambda _model: {"max_tokens": 32000})
    client = LLMClient(_spec())
    with pytest.raises(LLMModelMetadataError):
        _ = client.context_window


def test_context_window_fails_when_litellm_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未声明的私有 model 必须显式配置窗口，不能静默猜值。"""

    def _raise(_model: str) -> dict[str, int]:
        raise Exception("unknown model")

    monkeypatch.setattr(_GET_MODEL_INFO, _raise)
    client = LLMClient(_spec())
    with pytest.raises(LLMModelMetadataError):
        _ = client.context_window


def test_context_window_fails_when_litellm_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """litellm 返回无输入窗口字段时明确失败。"""
    monkeypatch.setattr(_GET_MODEL_INFO, lambda _model: {})
    client = LLMClient(_spec())
    with pytest.raises(LLMModelMetadataError):
        _ = client.context_window


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
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}
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


@pytest.mark.parametrize(
    ("provider_usage", "expected_hit", "expected_miss"),
    [
        (
            SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=2,
                total_tokens=122,
                cached_tokens=80,
            ),
            80,
            40,
        ),
        (
            SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=2,
                total_tokens=122,
                prompt_cache_hit_tokens=70,
                prompt_cache_miss_tokens=50,
            ),
            70,
            50,
        ),
        (
            SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=2,
                total_tokens=122,
                prompt_tokens_details=SimpleNamespace(cached_tokens=60),
            ),
            60,
            60,
        ),
    ],
)
def test_extract_usage_normalizes_prompt_cache_counters(
    provider_usage: Any,
    expected_hit: int,
    expected_miss: int,
) -> None:
    usage = _extract_usage(SimpleNamespace(usage=provider_usage))

    assert usage is not None
    assert usage.cached_prompt_tokens == expected_hit
    assert usage.cache_miss_prompt_tokens == expected_miss


def test_extract_usage_preserves_cache_counter_provenance_and_zero_state() -> None:
    usage = _extract_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=2,
                total_tokens=122,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            )
        )
    )

    assert usage is not None
    assert usage.cache_reporting_state == "reported_zero"
    assert usage.usage_source == "top_level"
    assert usage.cached_prompt_tokens_source == "prompt_tokens_details.cached_tokens"
    assert usage.cache_miss_prompt_tokens == 120
    assert usage.cache_miss_prompt_tokens_source == "derived_prompt_minus_cached"
    assert usage.cache_miss_prompt_tokens_derived is True


def test_extract_usage_does_not_let_litellm_private_zero_mask_openai_cache_details() -> None:
    usage = _extract_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=2,
                total_tokens=122,
                _cache_read_input_tokens=0,
                prompt_tokens_details=SimpleNamespace(cached_tokens=80),
            )
        )
    )

    assert usage is not None
    assert usage.cached_prompt_tokens == 80
    assert usage.cached_prompt_tokens_source == "prompt_tokens_details.cached_tokens"
    assert usage.cache_miss_prompt_tokens == 40


def test_extract_usage_reconciles_openai_inclusive_cache_write_buckets() -> None:
    usage = _extract_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=200,
                completion_tokens=8,
                total_tokens=208,
                prompt_tokens_details=SimpleNamespace(
                    cached_tokens=100,
                    cache_write_tokens=25,
                ),
            )
        )
    )

    assert usage is not None
    assert usage.cached_prompt_tokens == 100
    assert usage.cache_write_prompt_tokens == 25
    assert usage.input_other_prompt_tokens == 75
    assert usage.input_other_prompt_tokens_derived is True
    assert usage.input_token_accounting == "inclusive_total"
    assert usage.input_buckets_reconciled is True


def test_extract_usage_expands_anthropic_exclusive_input_components() -> None:
    usage = _extract_usage(
        SimpleNamespace(
            usage={
                "input_tokens": 100,
                "output_tokens": 8,
                "cache_read_input_tokens": 60,
                "cache_creation_input_tokens": 20,
            }
        )
    )

    assert usage is not None
    assert usage.prompt_tokens == 180
    assert usage.completion_tokens == 8
    assert usage.cached_prompt_tokens == 60
    assert usage.cache_write_prompt_tokens == 20
    assert usage.input_other_prompt_tokens == 100
    assert usage.input_token_accounting == "exclusive_components"
    assert usage.input_buckets_reconciled is True


def test_extract_usage_builds_deepseek_total_from_hit_and_miss() -> None:
    usage = _extract_usage(
        SimpleNamespace(
            usage={
                "completion_tokens": 5,
                "prompt_cache_hit_tokens": 70,
                "prompt_cache_miss_tokens": 50,
            }
        )
    )

    assert usage is not None
    assert usage.prompt_tokens == 120
    assert usage.cached_prompt_tokens == 70
    assert usage.cache_write_prompt_tokens is None
    assert usage.input_other_prompt_tokens == 50
    assert usage.input_token_accounting == "exclusive_components"
    assert usage.input_buckets_reconciled is False


def test_extract_usage_prefers_deepseek_native_cache_counters_on_conflict() -> None:
    usage = _extract_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=2,
                total_tokens=122,
                prompt_cache_hit_tokens=70,
                prompt_cache_miss_tokens=50,
                prompt_tokens_details=SimpleNamespace(cached_tokens=60),
                _cache_read_input_tokens=0,
            )
        )
    )

    assert usage is not None
    assert usage.cached_prompt_tokens == 70
    assert usage.cached_prompt_tokens_source == "prompt_cache_hit_tokens"
    assert usage.cache_miss_prompt_tokens == 50
    assert usage.cache_miss_prompt_tokens_source == "prompt_cache_miss_tokens"


def test_extract_stream_usage_reads_kimi_choice_usage() -> None:
    chunk = ChatCompletionChunk.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "kimi-k2.6",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 2,
                        "total_tokens": 122,
                        "cached_tokens": 80,
                    },
                }
            ],
        }
    )

    usage = _extract_stream_usage(chunk)

    assert usage == LLMUsage(
        prompt_tokens=120,
        completion_tokens=2,
        total_tokens=122,
        cached_prompt_tokens=80,
        cache_miss_prompt_tokens=40,
    )


def test_kimi_stream_uses_direct_transport_and_preserves_choice_cache_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    transport_events: list[Any] = []

    class FakeResponse:
        closed = False

        def __iter__(self) -> Any:
            return iter(
                [
                    SimpleNamespace(
                        id="chatcmpl-kimi-trace",
                        model="kimi-k2.6",
                        system_fingerprint="route-a",
                        service_tier="default",
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                usage={
                                    "prompt_tokens": 120,
                                    "completion_tokens": 2,
                                    "total_tokens": 122,
                                    "cached_tokens": 80,
                                },
                                delta=SimpleNamespace(
                                    content="ok",
                                    reasoning_content="private reasoning",
                                    tool_calls=[],
                                ),
                                finish_reason="stop",
                            )
                        ],
                    )
                ]
            )

        def close(self) -> None:
            self.closed = True

    response = FakeResponse()
    clients: list[Any] = []

    class FakeRawResponse:
        request_id = "request-kimi-trace"

        def parse(self) -> FakeResponse:
            return response

    class FakeRawCompletions:
        def create(self, **kwargs: Any) -> FakeRawResponse:
            seen.update(kwargs)
            return FakeRawResponse()

    class FakeCompletions:
        def __init__(self) -> None:
            self.with_raw_response = FakeRawCompletions()

    class FakeOpenAI:
        closed = False

        def __init__(self, **kwargs: Any) -> None:
            seen["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())
            clients.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("llm_providers.kimi_transport.openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        _COMPLETION,
        lambda **_kwargs: pytest.fail("Kimi stream must bypass LiteLLM"),
    )
    client = LLMClient(
        _spec(
            model="openai/kimi-k2.6",
            api_base="https://api.moonshot.cn/v1",
            defaults={"timeout": 12.5},
        )
    )

    events = list(
        client.stream(
            [{"role": "user", "content": "hi"}],
            prompt_cache_key="session-1",
            _diagnostic_callback=transport_events.append,
        )
    )

    done = next(event for event in events if isinstance(event, LLMTurnDone))
    assert done.usage == LLMUsage(
        prompt_tokens=120,
        completion_tokens=2,
        total_tokens=122,
        cached_prompt_tokens=80,
        cache_miss_prompt_tokens=40,
    )
    assert done.usage is not None
    assert done.usage.usage_source == "choice"
    assert done.usage.cached_prompt_tokens_source == "cached_tokens"
    assert done.usage.cache_miss_prompt_tokens_source == "derived_prompt_minus_cached"
    assert done.diagnostics is not None
    assert done.diagnostics.transport == "kimi_direct"
    # This test replaces the SDK itself, so no HTTPX request hook runs.  Final-wire
    # identity must stay unavailable instead of being guessed from SDK inputs.
    assert done.diagnostics.request_bytes is None
    assert done.diagnostics.request_fingerprint is None
    assert done.diagnostics.stream_open_ms is not None
    assert done.diagnostics.first_chunk_ms is not None
    assert done.diagnostics.first_reasoning_delta_ms is not None
    assert done.diagnostics.provider_request_id == "request-kimi-trace"
    assert done.diagnostics.response_id == "chatcmpl-kimi-trace"
    assert done.diagnostics.response_model == "kimi-k2.6"
    assert done.diagnostics.system_fingerprint == "route-a"
    assert done.diagnostics.service_tier == "default"
    observation = done.diagnostics.cache_observation
    assert observation is not None
    assert observation["model"]["provider"] == "kimi"
    assert observation["usage"]["input_total"] == 120
    assert observation["usage"]["cache_read"] == 80
    assert observation["outcome"]["status"] == "completed"
    assert [event.phase for event in transport_events] == [
        "request_started",
        "stream_opened",
        "first_chunk",
        "first_reasoning_delta",
    ]
    assert {event.request_fingerprint for event in transport_events} == {None}
    assert seen["model"] == "kimi-k2.6"
    assert seen["prompt_cache_key"] == "session-1"
    assert seen["stream"] is True
    assert seen["stream_options"] == {"include_usage": True}
    assert seen["timeout"] == 12.5
    assert "max_retries" not in seen
    assert response.closed is True
    assert len(clients) == 1
    assert clients[0].closed is True


def test_kimi_cancellable_stream_interrupts_response_header_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_started = threading.Event()
    request_cancelled = threading.Event()
    clients: list[Any] = []

    class FakeRawCompletions:
        async def create(self, **_kwargs: Any) -> Any:
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise

    class FakeCompletions:
        def __init__(self) -> None:
            self.with_raw_response = FakeRawCompletions()

    class FakeAsyncOpenAI:
        closed = False

        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())
            clients.append(self)

        async def close(self) -> None:
            self.closed = True

    class StopRequested(Exception):
        pass

    def cancel_check() -> None:
        if request_started.is_set():
            raise StopRequested("stop requested")

    monkeypatch.setattr("llm_providers.kimi_transport.openai.AsyncOpenAI", FakeAsyncOpenAI)
    client = LLMClient(
        _spec(
            model="openai/kimi-k2.7-code-highspeed",
            api_base="https://api.moonshot.cn/v1",
        )
    )

    started = time.monotonic()
    with pytest.raises(StopRequested, match="stop requested"):
        list(
            client.stream(
                [{"role": "user", "content": "hi"}],
                _cancel_check=cancel_check,
            )
        )

    assert time.monotonic() - started < 1.5
    assert request_cancelled.wait(1)
    assert len(clients) == 1
    assert clients[0].closed is True


def test_litellm_cancellable_stream_interrupts_response_header_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_started = threading.Event()
    request_cancelled = threading.Event()

    async def fake_acompletion(**_kwargs: Any) -> Any:
        request_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            request_cancelled.set()
            raise

    class StopRequested(Exception):
        pass

    def cancel_check() -> None:
        if request_started.is_set():
            raise StopRequested("stop requested")

    monkeypatch.setattr(
        "llm_providers.litellm_transport.litellm.acompletion",
        fake_acompletion,
    )
    client = LLMClient(_spec())

    started = time.monotonic()
    with pytest.raises(StopRequested, match="stop requested"):
        list(
            client.stream(
                [{"role": "user", "content": "hi"}],
                _cancel_check=cancel_check,
            )
        )

    assert time.monotonic() - started < 1.5
    assert request_cancelled.wait(1)


def test_kimi_cancellable_stream_allows_total_duration_beyond_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_closed = threading.Event()
    clients: list[FakeAsyncOpenAI] = []

    class ProgressingResponse:
        def __init__(self) -> None:
            self.remaining = 20

        def __aiter__(self) -> ProgressingResponse:
            return self

        async def __anext__(self) -> Any:
            if self.remaining == 0:
                raise StopAsyncIteration
            self.remaining -= 1
            await asyncio.sleep(0.005)
            if self.remaining:
                return SimpleNamespace(usage=None, choices=[])
            delta = SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[])
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(delta=delta, finish_reason="stop")],
            )

        async def close(self) -> None:
            response_closed.set()

    class FakeRawResponse:
        request_id = "deadline-request"

        async def create(self, **_kwargs: Any) -> FakeRawResponse:
            return self

        def parse(self) -> ProgressingResponse:
            return ProgressingResponse()

    class FakeCompletions:
        def __init__(self) -> None:
            self.with_raw_response = FakeRawResponse()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())
            clients.append(self)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "llm_providers.kimi_transport.openai.AsyncOpenAI",
        FakeAsyncOpenAI,
    )
    client = LLMClient(
        _spec(
            model="openai/kimi-k2.7-code-highspeed",
            api_base="https://api.moonshot.cn/v1",
            defaults={"network_retries": 1},
        ),
        stream_timeout_seconds=0.05,
    )

    started = time.monotonic()
    list(
        client.stream(
            [{"role": "user", "content": "hi"}],
            _cancel_check=lambda: None,
        )
    )

    assert time.monotonic() - started >= 0.08
    assert response_closed.wait(1)
    assert len(clients) == 1


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


def test_stream_timeout_uses_configured_pre_output_retry_budget() -> None:
    client = LLMClient(_spec(), network_retries=2, network_retry_backoff_seconds=0)

    assert client._retry_limit(LLMStreamTimeoutError("stalled")) == 2


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
    with pytest.raises(LLMStreamInterruptedAfterOutputError) as captured:
        next(stream)
    assert captured.value.reason_code == "stream_interrupted_after_output"
    assert calls == 1


def test_incomplete_response_errors_expose_stable_runtime_reason_codes() -> None:
    assert LLMEmptyResponseError.reason_code == "empty_model_response"
    assert LLMOutputBudgetExhaustedError.reason_code == "output_budget_exhausted"


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


def test_explicit_auth_status_maps_before_generic_provider_errors() -> None:
    assert isinstance(_map_exception(_StatusError(401)), LLMAuthError)
    assert isinstance(_map_exception(_StatusError(403)), LLMAuthError)


def _openai_rate_limit_error(
    error_type: str,
    *,
    headers: dict[str, str] | None = None,
) -> openai.RateLimitError:
    request = httpx.Request("POST", "https://api.moonshot.cn/v1/chat/completions")
    response = httpx.Response(429, request=request, headers=headers)
    return openai.RateLimitError(
        "provider returned HTTP 429",
        response=response,
        body={"error": {"message": "busy", "type": error_type}},
    )


def test_kimi_engine_overload_429_maps_to_transient_provider_error() -> None:
    mapped = _map_exception(
        _openai_rate_limit_error(
            "engine_overloaded_error",
            headers={"Retry-After": "3.5", "x-request-id": "request-overload-1"},
        )
    )

    assert isinstance(mapped, LLMTransientProviderError)
    assert mapped.provider_error_type == "engine_overloaded_error"
    assert mapped.status_code == 429
    assert mapped.retry_after_seconds == 3.5
    assert mapped.provider_request_id == "request-overload-1"


def test_generic_429_remains_rate_limit_error() -> None:
    mapped = _map_exception(_openai_rate_limit_error("rate_limit_error"))

    assert isinstance(mapped, LLMRateLimitError)


def test_kimi_direct_stream_retries_engine_overload_before_first_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_open_kimi_stream(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _openai_rate_limit_error("engine_overloaded_error")
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

    monkeypatch.setattr("llm_providers.client.open_kimi_stream", fake_open_kimi_stream)
    monkeypatch.setattr("llm_providers.client.random.uniform", lambda _low, high: high)
    monkeypatch.setattr("llm_providers.client.time.sleep", sleeps.append)
    monkeypatch.setattr(
        _COMPLETION,
        lambda **_kwargs: pytest.fail("Kimi retry must stay on the direct transport"),
    )
    client = LLMClient(
        _spec(
            model="openai/kimi-k2.7-code-highspeed",
            api_base="https://api.moonshot.cn/v1",
        ),
        network_retries=0,
        network_retry_backoff_seconds=0,
        engine_overload_retries=2,
        engine_overload_retry_backoff_seconds=2,
        engine_overload_retry_jitter_ratio=0.25,
    )

    events = list(client.stream([{"role": "user", "content": "hi"}]))

    assert calls == 3
    retries = [event for event in events if isinstance(event, LLMRetryScheduled)]
    assert len(retries) == 2
    assert [event.max_attempts for event in retries] == [3, 3]
    assert [event.delay_seconds for event in retries] == [2.5, 5.0]
    assert sleeps == [2.5, 5.0]
    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["ok"]


def test_kimi_engine_overload_honors_retry_after_and_emits_failure_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []
    diagnostics: list[Any] = []

    def fake_open_kimi_stream(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _openai_rate_limit_error(
                "engine_overloaded_error",
                headers={"Retry-After": "3.5", "x-request-id": "request-overload-1"},
            )
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

    monkeypatch.setattr("llm_providers.client.open_kimi_stream", fake_open_kimi_stream)
    monkeypatch.setattr("llm_providers.client.time.sleep", sleeps.append)
    client = LLMClient(
        _spec(
            model="openai/kimi-k2.7-code-highspeed",
            api_base="https://api.moonshot.cn/v1",
        ),
        engine_overload_retries=1,
        engine_overload_retry_backoff_seconds=20,
    )

    events = list(
        client.stream(
            [{"role": "user", "content": "hi"}],
            _diagnostic_callback=diagnostics.append,
        )
    )

    retry = next(event for event in events if isinstance(event, LLMRetryScheduled))
    assert retry.delay_seconds == 3.5
    assert sleeps == [3.5]
    failed = next(event for event in diagnostics if event.phase == "request_open_failed")
    assert failed.provider_error_type == "engine_overloaded_error"
    assert failed.status_code == 429
    assert failed.retry_after_seconds == 3.5
    assert failed.provider_request_id == "request-overload-1"


def test_kimi_generic_429_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_open_kimi_stream(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise _openai_rate_limit_error("rate_limit_reached_error")

    monkeypatch.setattr("llm_providers.client.open_kimi_stream", fake_open_kimi_stream)
    client = LLMClient(
        _spec(
            model="openai/kimi-k2.7-code-highspeed",
            api_base="https://api.moonshot.cn/v1",
        ),
        network_retries=2,
        network_retry_backoff_seconds=0,
        engine_overload_retries=2,
        engine_overload_retry_backoff_seconds=0,
    )

    with pytest.raises(LLMRateLimitError):
        list(client.stream([{"role": "user", "content": "hi"}]))
    assert calls == 1


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

    with pytest.raises(LLMEmptyResponseError):
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


def test_stream_emits_content_free_reasoning_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_reasoning = "private chain of thought"

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
                            finish_reason=None,
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="完成",
                                reasoning_content=None,
                                tool_calls=[],
                            ),
                            finish_reason="stop",
                        )
                    ],
                ),
            ]
        )

    monkeypatch.setattr(_COMPLETION, fake_completion)
    events = list(LLMClient(_spec(), network_retries=0).stream([{"role": "user", "content": "hi"}]))

    activity = next(event for event in events if isinstance(event, LLMActivity))
    assert activity.kind == "reasoning"
    assert secret_reasoning not in repr(activity)
    assert any(isinstance(event, LLMTextDelta) and event.text == "完成" for event in events)


def test_stream_rejects_reasoning_only_output_budget_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_completion(**_kwargs: Any) -> Any:
        return iter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content="hidden reasoning",
                                tool_calls=[],
                            ),
                            finish_reason="length",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(_COMPLETION, fake_completion)
    client = LLMClient(_spec(), network_retries=0)

    with pytest.raises(LLMOutputBudgetExhaustedError):
        list(client.stream([{"role": "user", "content": "hi"}]))
