"""``LLMClient``：统一 LLM provider 接口、流式事件与错误模型。

默认 transport 使用 LiteLLM；存在已确认协议差异的 provider 可在本层接入专用
transport，避免兼容逻辑泄漏到 Conversation 等上层模块。

提供两个公开方法：

- :meth:`LLMClient.complete` — 同步一次性返回完整回复
- :meth:`LLMClient.stream`   — 流式返回事件序列（generator 风格）

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.2 与
docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.5。
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from functools import cached_property
from typing import Any, cast

import litellm

from .call_observation import current_call, observed_generate, observed_stream
from .chat_request import prepare_litellm_request
from .deepseek_vision_compat import is_deepseek_vision_spec
from .error_normalization import map_provider_error as _normalize_provider_error
from .errors import (
    LLMEmptyResponseError,
    LLMError,
    LLMModelMetadataError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
    LLMProviderError,
    LLMStreamInterruptedAfterOutputError,
    LLMTransientProviderError,
)
from .kimi_transport import (
    open_cancellable_kimi_stream,
    uses_kimi_stream_transport,
)
from .litellm_transport import (
    complete_cancellable_litellm,
    open_cancellable_litellm_stream,
)
from .prompt_cache_observation import (
    build_prompt_cache_observation,
    emit_prompt_cache_observation,
)
from .protocol import (
    DEFAULT_MODEL_RETRIES,
    LanguageModelCapabilities,
    ModelCallControl,
    ModelRequest,
    ModelResponse,
)
from .reasoning import build_provider_reasoning_state, reasoning_detail_objects
from .request_witness import ProviderRequestWitness
from .responses import stream_responses
from .responses.generate import generate_responses
from .spec import ProviderSpec
from .stream_events import (
    LLMActivity,
    LLMResponseDiagnostics,
    LLMRetryScheduled,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTransportDiagnostic,
    LLMTurnDone,
    LLMUsage,
    ProviderAssistantTurn,
    ProviderToolCall,
)
from .timeouts import ModelTimeoutPolicy, controlled_cancel, resolve_timeout_policy
from .usage import extract_stream_usage, extract_usage
from .usage import usage_field as _usage_field

DEFAULT_NETWORK_RETRIES = DEFAULT_MODEL_RETRIES
"""可安全重试的模型请求默认重试次数。"""

DEFAULT_NETWORK_RETRY_BACKOFF_SECONDS = 0.5
"""网络重试的初始退避秒数；后续重试按 2 倍递增。"""

DEFAULT_ENGINE_OVERLOAD_RETRIES = DEFAULT_MODEL_RETRIES
"""引擎过载在未产出结果时的默认重试次数。"""

DEFAULT_ENGINE_OVERLOAD_RETRY_BACKOFF_SECONDS = 2.0
"""Provider 未给 ``Retry-After`` 时，引擎过载的初始退避秒数。"""

DEFAULT_ENGINE_OVERLOAD_RETRY_JITTER_RATIO = 0.25
"""引擎过载指数退避附加的随机抖动上限比例。"""

MAX_ENGINE_OVERLOAD_RETRY_DELAY_SECONDS = 30.0
"""交互请求单次过载退避的上限，避免异常响应让一轮会话长期挂起。"""

DEFAULT_STREAM_TIMEOUT_SECONDS = 300.0
"""交互流默认阶段静默时限；没有整次调用累计时限。"""

_HIDDEN_ACTIVITY_EMIT_INTERVAL_SECONDS = 2.0
"""不可见上游增量的状态事件节流间隔，避免把推理 token 逐个推给 UI。"""

logger = logging.getLogger(__name__)


def _extract_usage(response: Any) -> LLMUsage | None:
    return extract_usage(response)


def _extract_stream_usage(chunk: Any) -> LLMUsage | None:
    return extract_stream_usage(chunk)


@dataclass(frozen=True)
class ResolvedModelLimits:
    """LLMClient 实际采用的模型限制及其证据来源。"""

    context_window: int
    context_window_source: str


class _ProviderStreamChunkError(RuntimeError):
    """Content-free wrapper for an HTTP-200 stream error payload."""

    def __init__(self, payload: Any) -> None:
        super().__init__("Provider returned an error event in the response stream.")
        error = payload if isinstance(payload, dict) else {}
        self.body = {"error": error}
        code = error.get("code")
        self.status_code = code if isinstance(code, int) and 400 <= code <= 599 else None


def _provider_id_for_spec(spec: ProviderSpec) -> str | None:
    if spec.provider_id is not None:
        return spec.provider_id
    if spec.model.startswith("openrouter/"):
        return "openrouter"
    if spec.model.startswith("deepseek/"):
        return "deepseek"
    if is_deepseek_vision_spec(spec):
        return "deepseek"
    if spec.model.startswith("openai/kimi-"):
        return "kimi"
    return None


def _raise_stream_chunk_error(chunk: Any) -> None:
    error = _usage_field(chunk, "error")
    if error:
        raise _ProviderStreamChunkError(error)


def _present_delta_field_names(delta: Any) -> set[str]:
    """Return populated delta field names without retaining or logging values."""
    data: dict[str, Any] | None = None
    model_dump = getattr(delta, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(exclude_none=True)
            if isinstance(dumped, dict):
                data = dumped
        except Exception:
            data = None
    if data is None:
        raw = getattr(delta, "__dict__", None)
        if isinstance(raw, dict):
            data = raw
    if data is None:
        return set()

    present: set[str] = set()
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, (str, bytes, list, tuple, dict, set)) and not value:
            continue
        present.add(str(key))
    return present


def _response_text_field(value: Any, name: str) -> str | None:
    raw = _usage_field(value, name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _response_cost(value: Any) -> tuple[float | None, str | None]:
    direct = _usage_field(value, "response_cost")
    if isinstance(direct, (int, float)) and not isinstance(direct, bool) and direct >= 0:
        return float(direct), "response.response_cost"
    hidden = _usage_field(value, "_hidden_params")
    nested = _usage_field(hidden, "response_cost")
    if isinstance(nested, (int, float)) and not isinstance(nested, bool) and nested >= 0:
        return float(nested), "response._hidden_params.response_cost"
    return None, None


def _emit_transport_diagnostic(
    callback: Callable[[LLMTransportDiagnostic], None] | None,
    event: LLMTransportDiagnostic,
) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        logger.warning("LLM transport diagnostic callback failed", exc_info=True)


def _map_exception(e: Exception, *, provider_id: str | None = None) -> LLMError:
    """Compatibility alias for tests that imported the former private helper."""
    return _normalize_provider_error(e, provider_id=provider_id)


def _map_finish_reason(finish_reason: str | None) -> str:
    """把 OpenAI / LiteLLM 的 ``finish_reason`` 标准化为本项目的 ``stop_reason``。

    标准化值参见 :class:`LLMTurnDone.stop_reason` 文档。
    """
    if finish_reason in ("stop", "end_turn"):
        return "end_turn"
    if finish_reason in ("tool_calls", "tool_use"):
        return "tool_use"
    if finish_reason in ("length", "max_tokens"):
        return "max_tokens"
    return "other"


class LLMClient:
    """统一的 LLM 调用客户端。

    Args:
        spec: ``ProviderSpec`` 实例，决定调用哪个 Provider、用什么 model、
            用什么 key。
    """

    def __init__(
        self,
        spec: ProviderSpec,
        *,
        network_retries: int = DEFAULT_NETWORK_RETRIES,
        network_retry_backoff_seconds: float = DEFAULT_NETWORK_RETRY_BACKOFF_SECONDS,
        engine_overload_retries: int = DEFAULT_ENGINE_OVERLOAD_RETRIES,
        engine_overload_retry_backoff_seconds: float = (
            DEFAULT_ENGINE_OVERLOAD_RETRY_BACKOFF_SECONDS
        ),
        engine_overload_retry_jitter_ratio: float = (DEFAULT_ENGINE_OVERLOAD_RETRY_JITTER_RATIO),
        engine_overload_max_retry_delay_seconds: float = (MAX_ENGINE_OVERLOAD_RETRY_DELAY_SECONDS),
        stream_timeout_seconds: float | None = None,
    ):
        if network_retries < 0:
            raise ValueError("network_retries 不能小于 0")
        if network_retry_backoff_seconds < 0:
            raise ValueError("network_retry_backoff_seconds 不能小于 0")
        if engine_overload_retries < 0:
            raise ValueError("engine_overload_retries 不能小于 0")
        if engine_overload_retry_backoff_seconds < 0:
            raise ValueError("engine_overload_retry_backoff_seconds 不能小于 0")
        if engine_overload_retry_jitter_ratio < 0:
            raise ValueError("engine_overload_retry_jitter_ratio 不能小于 0")
        if engine_overload_max_retry_delay_seconds <= 0:
            raise ValueError("engine_overload_max_retry_delay_seconds 必须大于 0")
        if stream_timeout_seconds is not None and stream_timeout_seconds <= 0:
            raise ValueError("stream_timeout_seconds 必须大于 0")
        self.spec = spec
        self._network_retries = network_retries
        self._network_retry_backoff_seconds = network_retry_backoff_seconds
        self._engine_overload_retries = engine_overload_retries
        self._engine_overload_retry_backoff_seconds = engine_overload_retry_backoff_seconds
        self._engine_overload_retry_jitter_ratio = engine_overload_retry_jitter_ratio
        self._engine_overload_max_retry_delay_seconds = engine_overload_max_retry_delay_seconds
        self._stream_timeout_seconds = stream_timeout_seconds

    @property
    def capabilities(self) -> LanguageModelCapabilities:
        route = self.spec.responses
        return LanguageModelCapabilities(
            assistant_message_phase=route.assistant_message_phase if route else "unavailable",
            # Both adapters send the complete client-owned history. Responses
            # rejects server-side conversation storage / previous_response_id;
            # opaque reasoning items are replayed explicitly in that request.
            input_usage_scope="request",
            context_checkpoint_mode="replace_complete_groups",
        )

    @property
    def context_window(self) -> int:
        """当前 model 的最大输入 token 窗口。"""
        return self.model_limits.context_window

    @cached_property
    def model_limits(self) -> ResolvedModelLimits:
        """按显式配置、LiteLLM 输入元数据的顺序解析并缓存模型限制。"""
        if self.spec.context_window is not None:
            limits = ResolvedModelLimits(
                self.spec.context_window,
                self.spec.context_window_source or "provider_spec",
            )
            logger.info(
                "resolved model limits: model=%s context_window=%d source=%s",
                self.spec.model,
                limits.context_window,
                limits.context_window_source,
            )
            return limits
        try:
            info = litellm.get_model_info(self.spec.model)
        except Exception as exc:
            raise LLMModelMetadataError(
                f"无法解析模型 {self.spec.model!r} 的上下文窗口；请在模型目录或运行配置中声明。"
            ) from exc
        if isinstance(info, dict):
            win = info.get("max_input_tokens")
            if win:
                try:
                    parsed = int(win)
                    if parsed > 0:
                        limits = ResolvedModelLimits(parsed, "litellm.max_input_tokens")
                        logger.info(
                            "resolved model limits: model=%s context_window=%d source=%s",
                            self.spec.model,
                            limits.context_window,
                            limits.context_window_source,
                        )
                        return limits
                except (ValueError, TypeError):
                    pass
        raise LLMModelMetadataError(
            f"模型 {self.spec.model!r} 缺少有效的 max_input_tokens；请显式声明上下文窗口。"
        )

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        """同步调用 LLM，返回完整的 assistant 文本回复。

        Args:
            messages: OpenAI 风格的消息列表，每条形如
                ``{"role": "user" | "system" | "assistant" | "tool", "content": "..."}``；
                ``role="tool"`` 还需带 ``tool_call_id``；含工具调用的 assistant
                消息还可能带 ``tool_calls`` 字段。
            **overrides: 本次调用临时覆盖 :attr:`ProviderSpec.defaults`
                里的参数（如 ``temperature=0.9``）。

        Returns:
            assistant 消息的 ``content`` 字符串。

        Raises:
            LLMAuthError: API key 错或缺失。
            LLMRateLimitError: 触发限速（LiteLLM 自带 retry 用尽后）。
            LLMNetworkError: 网络错或超时。
            LLMBadRequestError: 请求格式错或上下文超长。
            LLMProviderError: 其他 Provider 侧错误。

        Note:
            本方法**不支持工具调用**——仅做单次同步问答。如需工具调用走
            :meth:`stream`（``Conversation.send`` 内部走 stream 拼接得到完整文本）。
        """
        return self.generate(ModelRequest(messages=messages, settings=overrides)).text

    @observed_generate
    def generate(self, request: ModelRequest) -> ModelResponse:
        """Execute one provider-neutral non-streaming model request."""

        started = time.perf_counter()
        cancel_check = controlled_cancel(
            request.control.cancel_check, request.control.deadline_monotonic
        )
        policy = resolve_timeout_policy(
            self.spec.defaults,
            request.settings,
            streaming=False,
            provider_timeout_is_default=self.spec.timeout_is_default,
            explicit=request.control.timeout_policy,
        )
        if (observation := current_call.get()) is not None:
            observation.timeout_policy = asdict(policy)
            observation.business_deadline = request.control.deadline_monotonic is not None
            observation.business_remaining_seconds = (
                max(0.0, request.control.deadline_monotonic - time.monotonic())
                if request.control.deadline_monotonic is not None
                else None
            )
        request = replace(
            request,
            control=replace(request.control, cancel_check=cancel_check, timeout_policy=policy),
        )
        max_retries = (
            max(self._network_retries, self._engine_overload_retries)
            if request.control.allow_retries
            else 0
        )
        try:
            for attempt in range(max_retries + 1):
                try:
                    if cancel_check is not None:
                        cancel_check()
                    settings = dict(request.settings)
                    if request.tools:
                        settings["tools"] = request.tools
                    response = (
                        generate_responses(
                            self.spec,
                            request,
                            timeout_seconds=policy.response_seconds,
                            attempt=attempt,
                        )
                        if self.spec.responses is not None
                        else self._call_once(
                            self.spec,
                            request.messages,
                            cancel_check=cancel_check,
                            previous_request_witness=request.control.previous_request_witness,
                            transcript_item_domains=request.control.transcript_item_domains,
                            wait_policy=policy,
                            **settings,
                        )
                    )
                    if cancel_check is not None:
                        cancel_check()
                    return self._observe_response(
                        response,
                        request=request,
                        started=started,
                    )
                except (LLMNetworkError, LLMTransientProviderError) as exc:
                    retry_limit = self._retry_limit(exc) if request.control.allow_retries else 0
                    if request.control.retry_limit is not None:
                        retry_limit = min(retry_limit, max(0, request.control.retry_limit))
                    if attempt >= retry_limit:
                        observation = current_call.get()
                        if observation is not None:
                            observation.retry_stop = (
                                "retries_disabled"
                                if not request.control.allow_retries
                                else "server_cooldown"
                                if (
                                    isinstance(exc, LLMTransientProviderError)
                                    and exc.retry_after_seconds is not None
                                    and exc.retry_after_seconds
                                    > self._engine_overload_max_retry_delay_seconds
                                )
                                else "retry_exhausted"
                            )
                        raise
                    delay = self._retry_delay(exc, attempt)
                    self._wait_before_retry(
                        exc, attempt, retry_limit, delay, cancel_check=cancel_check
                    )
        except BaseException as exc:
            try:
                failed_diagnostics: LLMResponseDiagnostics | None = None
                failed_witness = getattr(exc, "request_witness", None)
                previous_witness = request.control.previous_request_witness
                if isinstance(failed_witness, ProviderRequestWitness):
                    failed_diagnostics = LLMResponseDiagnostics(
                        transport="responses" if self.spec.responses is not None else "litellm",
                        request_bytes=failed_witness.request_bytes,
                        request_fingerprint=failed_witness.request_fingerprint,
                        request_relation=failed_witness.relation_to(previous_witness),
                        common_prefix_items=failed_witness.common_prefix_items(previous_witness),
                        previous_request_items=(
                            len(previous_witness.transcript_item_digests)
                            if previous_witness is not None
                            else None
                        ),
                        first_divergence_index=failed_witness.first_divergence_index(
                            previous_witness
                        ),
                        first_divergence_role=failed_witness.first_divergence_role(
                            previous_witness
                        ),
                        first_divergence_domain=failed_witness.first_divergence_domain(
                            previous_witness
                        ),
                        first_divergence_previous_domain=(
                            failed_witness.first_divergence_previous_domain(previous_witness)
                        ),
                        first_divergence_current_domain=(
                            failed_witness.first_divergence_current_domain(previous_witness)
                        ),
                        contributor_mapping_precision=(
                            failed_witness.contributor_mapping_precision
                        ),
                        lost_prefix_items=failed_witness.lost_prefix_items(previous_witness),
                        request_witness=failed_witness,
                    )
                observation = build_prompt_cache_observation(
                    requested_model=self.spec.model,
                    provider_id=_provider_id_for_spec(self.spec),
                    message_count=len(request.messages),
                    tool_count=len(request.tools or []),
                    usage=None,
                    diagnostics=failed_diagnostics,
                    control=request.control,
                    total_elapsed_ms=round((time.perf_counter() - started) * 1000),
                    status="failed",
                    error_type=type(exc).__name__,
                )
                emit_prompt_cache_observation(observation)
            except Exception:
                logger.warning("prompt cache failure observation build failed", exc_info=True)
            raise
        raise AssertionError("transient retry loop should always return or raise")

    def _observe_response(
        self,
        response: ModelResponse,
        *,
        request: ModelRequest,
        started: float,
    ) -> ModelResponse:
        diagnostics = response.diagnostics or LLMResponseDiagnostics(transport="litellm")
        try:
            observation = build_prompt_cache_observation(
                requested_model=self.spec.model,
                provider_id=_provider_id_for_spec(self.spec),
                message_count=len(request.messages),
                tool_count=len(request.tools or []),
                usage=response.usage,
                diagnostics=diagnostics,
                control=request.control,
                total_elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
            emit_prompt_cache_observation(observation)
            diagnostics = replace(diagnostics, cache_observation=observation)
        except Exception:
            logger.warning("prompt cache response observation build failed", exc_info=True)
        return replace(response, diagnostics=diagnostics)

    @observed_stream
    def stream(
        self,
        request: ModelRequest | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        """流式调用 LLM，逐个 yield :data:`LLMStreamEvent`。

        典型用法（消费方按事件类型分派）::

            for ev in client.stream(messages, tools=tool_specs):
                if isinstance(ev, LLMTextDelta):
                    print(ev.text, end="", flush=True)
                elif isinstance(ev, LLMToolCallDelta):
                    accumulate_tool_call(ev)
                elif isinstance(ev, LLMTurnDone):
                    stop_reason = ev.stop_reason

        Args:
            messages: 同 :meth:`complete`。
            tools: 可选的 OpenAI tool calling 风格 tools 数组。每项形如
                ``{"type": "function", "function": {"name", "description", "parameters"}}``。
                传 ``None`` 或 ``[]`` 表示本次不开放工具调用。
            **overrides: 同 :meth:`complete`。

        Yields:
            :class:`LLMTextDelta` / :class:`LLMToolCallDelta` /
            :class:`LLMTurnDone` 之一。完整 turn 至少 yield 一次
            :class:`LLMTurnDone`（除非中途异常）。

        Raises:
            同 :meth:`complete`，异常可能在初始化或迭代中任一时机抛出。
        """
        if isinstance(request, ModelRequest):
            if tools is not None or overrides:
                raise TypeError("ModelRequest 已包含 tools/settings，不能再传 legacy stream 参数")
            messages = request.messages
            tools = request.tools
            overrides = dict(request.settings)
            diagnostic_callback = request.control.diagnostic_callback
            cancel_check = request.control.cancel_check
            previous_request_witness = request.control.previous_request_witness
            transcript_item_domains = request.control.transcript_item_domains
            control = request.control
            if request.cache_key and uses_kimi_stream_transport(self.spec):
                overrides.setdefault("prompt_cache_key", request.cache_key)
        else:
            messages = request
            control = ModelCallControl()
            diagnostic_callback = cast(
                Callable[[LLMTransportDiagnostic], None] | None,
                overrides.pop("_diagnostic_callback", None),
            )
            cancel_check = cast(
                Callable[[], None] | None,
                overrides.pop("_cancel_check", None),
            )
            previous_request_witness = cast(
                ProviderRequestWitness | None,
                overrides.pop("_previous_request_witness", None),
            )
            transcript_item_domains = None
        cancel_check = controlled_cancel(cancel_check, control.deadline_monotonic)
        policy = resolve_timeout_policy(
            self.spec.defaults,
            overrides,
            streaming=True,
            provider_timeout_is_default=self.spec.timeout_is_default,
            explicit=control.timeout_policy,
            legacy_stream_seconds=self._stream_timeout_seconds,
        )
        if (observation := current_call.get()) is not None:
            observation.timeout_policy = asdict(policy)
            observation.business_deadline = control.deadline_monotonic is not None
            observation.business_remaining_seconds = (
                max(0.0, control.deadline_monotonic - time.monotonic())
                if control.deadline_monotonic is not None
                else None
            )
        upstream = self._stream_with_network_retries(
            self.spec,
            messages,
            tools=tools,
            diagnostic_callback=diagnostic_callback,
            cancel_check=cancel_check,
            previous_request_witness=previous_request_witness,
            transcript_item_domains=transcript_item_domains,
            wait_policy=policy,
            allow_retries=control.allow_retries,
            allow_output_retries=control.allow_output_retries,
            retry_limit_remaining=control.retry_limit,
            retry_attempt_offset=control.retry_attempt_offset,
            **overrides,
        )
        return self._observe_stream(
            upstream,
            messages=messages,
            tools=tools,
            control=control,
        )

    def _observe_stream(
        self,
        upstream: Iterator[LLMStreamEvent],
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        control: ModelCallControl,
    ) -> Iterator[LLMStreamEvent]:
        """Attach and emit exactly one observation per logical stream call."""

        started = time.perf_counter()
        observation_emitted = False
        try:
            for event in upstream:
                if not isinstance(event, LLMTurnDone):
                    yield event
                    continue
                diagnostics = event.diagnostics or LLMResponseDiagnostics()
                try:
                    observation = build_prompt_cache_observation(
                        requested_model=self.spec.model,
                        provider_id=_provider_id_for_spec(self.spec),
                        message_count=len(messages),
                        tool_count=len(tools or []),
                        usage=event.usage,
                        diagnostics=diagnostics,
                        control=control,
                        total_elapsed_ms=round((time.perf_counter() - started) * 1000),
                    )
                    emit_prompt_cache_observation(observation)
                    observation_emitted = True
                    diagnostics = replace(diagnostics, cache_observation=observation)
                except Exception:
                    logger.warning("prompt cache stream observation build failed", exc_info=True)
                yield replace(event, diagnostics=diagnostics)
        except BaseException as exc:
            if observation_emitted:
                raise
            try:
                failed_usage = getattr(exc, "usage", None)
                failed_diagnostics = getattr(exc, "diagnostics", None)
                observation = build_prompt_cache_observation(
                    requested_model=self.spec.model,
                    provider_id=_provider_id_for_spec(self.spec),
                    message_count=len(messages),
                    tool_count=len(tools or []),
                    usage=failed_usage if isinstance(failed_usage, LLMUsage) else None,
                    diagnostics=(
                        failed_diagnostics
                        if isinstance(failed_diagnostics, LLMResponseDiagnostics)
                        else None
                    ),
                    control=control,
                    total_elapsed_ms=round((time.perf_counter() - started) * 1000),
                    status="failed",
                    error_type=type(exc).__name__,
                )
                emit_prompt_cache_observation(observation)
            except Exception:
                logger.warning("prompt cache failure observation build failed", exc_info=True)
            raise
        finally:
            close = getattr(upstream, "close", None)
            if callable(close):
                close()

    def _stream_with_network_retries(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        diagnostic_callback: Callable[[LLMTransportDiagnostic], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
        previous_request_witness: ProviderRequestWitness | None = None,
        transcript_item_domains: tuple[str, ...] | None = None,
        allow_retries: bool = True,
        allow_output_retries: bool = False,
        retry_limit_remaining: int | None = None,
        retry_attempt_offset: int = 0,
        wait_policy: ModelTimeoutPolicy | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        """Retry an uncommitted response using one bounded attempt budget."""
        max_retries = (
            max(self._network_retries, self._engine_overload_retries) if allow_retries else 0
        )
        if retry_limit_remaining is not None:
            max_retries = min(max_retries, max(0, retry_limit_remaining))
        relation_witness = previous_request_witness
        for attempt in range(max_retries + 1):
            emitted = False
            completed = False
            should_retry = False
            retry_error: LLMNetworkError | LLMTransientProviderError | None = None
            retry_limit = 0
            attempt_witness: ProviderRequestWitness | None = None

            def observe_diagnostic(event: LLMTransportDiagnostic) -> None:
                nonlocal attempt_witness
                if event.request_witness is not None:
                    attempt_witness = event.request_witness
                _emit_transport_diagnostic(diagnostic_callback, event)

            upstream = self._stream_once(
                spec,
                messages,
                tools=tools,
                diagnostic_callback=observe_diagnostic,
                diagnostic_attempt=attempt + 1,
                cancel_check=cancel_check,
                previous_request_witness=relation_witness,
                transcript_item_domains=transcript_item_domains,
                wait_policy=wait_policy,
                **overrides,
            )
            try:
                for event in upstream:
                    # 活动信号不包含正文或工具调用；它不应改变“尚未产出可用结果”
                    # 的安全重试语义。
                    # Tool-call deltas also remain transient until a terminal chunk
                    # closes the response; Conversation resets their accumulator at
                    # the retry boundary.
                    if isinstance(event, (LLMTextDelta, LLMTurnDone)):
                        emitted = True
                    if isinstance(event, LLMTurnDone):
                        completed = True
                    yield event
                return
            except (LLMNetworkError, LLMTransientProviderError) as exc:
                retry_limit = min(self._retry_limit(exc), max_retries) if allow_retries else 0
                if completed or (emitted and not allow_output_retries):
                    observation = current_call.get()
                    if observation is not None:
                        observation.retry_stop = "output_committed"
                    interrupted = LLMStreamInterruptedAfterOutputError(
                        "provider stream failed after visible output"
                    )
                    interrupted.error_evidence = exc.error_evidence
                    raise interrupted from exc
                if attempt >= retry_limit:
                    observation = current_call.get()
                    if observation is not None:
                        observation.retry_stop = (
                            "retries_disabled"
                            if not allow_retries
                            else "server_cooldown"
                            if (
                                isinstance(exc, LLMTransientProviderError)
                                and exc.retry_after_seconds is not None
                                and exc.retry_after_seconds
                                > self._engine_overload_max_retry_delay_seconds
                            )
                            else "retry_exhausted"
                        )
                    raise
                should_retry = True
                retry_error = exc
            finally:
                close = getattr(upstream, "close", None)
                if callable(close):
                    close()
            if should_retry:
                assert retry_error is not None
                delay = self._retry_delay(retry_error, attempt)
                observation = current_call.get()
                if observation is not None:
                    observation.plan_retry(retry_error, delay)
                yield LLMRetryScheduled(
                    attempt=retry_attempt_offset + attempt + 2,
                    max_attempts=retry_attempt_offset + retry_limit + 1,
                    delay_seconds=delay,
                )
                self._wait_before_retry(
                    retry_error,
                    attempt,
                    retry_limit,
                    delay,
                    cancel_check=cancel_check,
                    record_plan=False,
                )
                if attempt_witness is not None:
                    relation_witness = attempt_witness

    def _network_retry_delay(self, attempt: int) -> float:
        return self._network_retry_backoff_seconds * (2**attempt)

    @staticmethod
    def _is_engine_overload(error: LLMError) -> bool:
        return isinstance(error, LLMTransientProviderError) and error.provider_error_type in {
            "engine_overloaded_error",
            "server_is_overloaded",
            "provider_overloaded",
        }

    def _retry_limit(self, error: LLMError) -> int:
        if (
            isinstance(error, LLMTransientProviderError)
            and error.retry_after_seconds is not None
            and error.retry_after_seconds > self._engine_overload_max_retry_delay_seconds
        ):
            # A bounded automatic wait must not violate a longer server cooldown.
            return 0
        if self._is_engine_overload(error):
            return self._engine_overload_retries
        return self._network_retries

    def _retry_delay(self, error: LLMError, attempt: int) -> float:
        if isinstance(error, LLMTransientProviderError) and error.retry_after_seconds is not None:
            return max(0.0, error.retry_after_seconds)
        if not self._is_engine_overload(error):
            return self._network_retry_delay(attempt)

        assert isinstance(error, LLMTransientProviderError)
        base_delay = self._engine_overload_retry_backoff_seconds * (2**attempt)
        jitter = random.uniform(
            0.0,
            base_delay * self._engine_overload_retry_jitter_ratio,
        )
        return min(
            base_delay + jitter,
            self._engine_overload_max_retry_delay_seconds,
        )

    def _wait_before_retry(
        self,
        error: LLMError,
        attempt: int,
        retry_limit: int,
        delay: float,
        *,
        cancel_check: Callable[[], None] | None = None,
        record_plan: bool = True,
    ) -> None:
        observation = current_call.get()
        if observation is not None and record_plan:
            observation.plan_retry(error, delay)
        logger.warning(
            "LLM transient request failed before usable output; retrying model=%s "
            "retry=%d/%d delay_seconds=%.3f provider_error_type=%s "
            "status_code=%s retry_after_seconds=%s provider_request_id=%s",
            self.spec.model,
            attempt + 1,
            retry_limit,
            delay,
            getattr(error, "provider_error_type", None),
            getattr(error, "status_code", None),
            getattr(error, "retry_after_seconds", None),
            getattr(error, "provider_request_id", None),
        )
        if delay <= 0:
            return
        if cancel_check is None:
            time.sleep(delay)
            return
        deadline = time.monotonic() + delay
        while True:
            cancel_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def _call_once(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        *,
        cancel_check: Callable[[], None] | None = None,
        previous_request_witness: ProviderRequestWitness | None = None,
        transcript_item_domains: tuple[str, ...] | None = None,
        wait_policy: ModelTimeoutPolicy | None = None,
        **overrides: Any,
    ) -> ModelResponse:
        """单次同步调用 LiteLLM。

        Note:
            这是一个**故意抽出来的私有方法**，用于为未来的多 Provider
            fallback 留口子：届时只需在外层 wrap
            ``for spec in specs: try: _call_once(...) except: continue``，
            不需要改 :meth:`complete` 的公开签名。
        """
        kwargs: dict[str, Any] = {**spec.defaults, **overrides}
        # LiteLLM/OpenAI SDK 自带重试会与本层的有界重试相乘。统一关闭内层重试，
        # 让 attempt 数、总等待时间与可观测状态只有一个所有者。
        kwargs["max_retries"] = 0
        policy = wait_policy or resolve_timeout_policy(
            spec.defaults,
            overrides,
            streaming=False,
            provider_timeout_is_default=spec.timeout_is_default,
        )
        kwargs["timeout"] = policy.http_timeout()
        request_messages, kwargs = prepare_litellm_request(spec, messages, kwargs)
        prepared_item_domains = (
            transcript_item_domains
            if transcript_item_domains is not None
            and len(transcript_item_domains) == len(request_messages)
            else None
        )
        try:
            if cancel_check is not None:
                cancel_check()
            completion = complete_cancellable_litellm(
                spec,
                request_messages,
                kwargs,
                cancel_check or (lambda: None),
                transcript_item_domains=prepared_item_domains,
                timeout_policy=policy,
            )
        except BaseException as e:
            if cancel_check is not None:
                try:
                    cancel_check()
                except BaseException as cancelled:
                    failed_witness = getattr(e, "request_witness", None)
                    if isinstance(failed_witness, ProviderRequestWitness):
                        with suppress(Exception):
                            cancelled.request_witness = failed_witness  # type: ignore[attr-defined]
                    raise
            if not isinstance(e, Exception):
                raise
            mapped = _map_exception(e, provider_id=_provider_id_for_spec(spec))
            failed_witness = getattr(e, "request_witness", None)
            if isinstance(failed_witness, ProviderRequestWitness):
                with suppress(Exception):
                    mapped.request_witness = failed_witness  # type: ignore[attr-defined]
            raise mapped from None

        if cancel_check is not None:
            cancel_check()
        response = completion.response
        request_witness = completion.request_witness
        request_relation = "unavailable"
        common_prefix_items: int | None = None
        previous_request_items = (
            len(previous_request_witness.transcript_item_digests)
            if previous_request_witness is not None
            else None
        )
        first_divergence_index: int | None = None
        first_divergence_role: str | None = None
        first_divergence_domain: str | None = None
        first_divergence_previous_domain: str | None = None
        first_divergence_current_domain: str | None = None
        contributor_mapping_precision = "generic"
        lost_prefix_items: int | None = None
        if request_witness is not None:
            contributor_mapping_precision = request_witness.contributor_mapping_precision
            request_relation = request_witness.relation_to(previous_request_witness)
            common_prefix_items = request_witness.common_prefix_items(previous_request_witness)
            first_divergence_index = request_witness.first_divergence_index(
                previous_request_witness
            )
            first_divergence_role = request_witness.first_divergence_role(previous_request_witness)
            first_divergence_domain = request_witness.first_divergence_domain(
                previous_request_witness
            )
            first_divergence_previous_domain = request_witness.first_divergence_previous_domain(
                previous_request_witness
            )
            first_divergence_current_domain = request_witness.first_divergence_current_domain(
                previous_request_witness
            )
            lost_prefix_items = request_witness.lost_prefix_items(previous_request_witness)

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("LLM 返回了空 content。")
        response_cost, response_cost_source = _response_cost(response)
        return ModelResponse(
            text=content,
            usage=_extract_usage(response),
            diagnostics=LLMResponseDiagnostics(
                transport="litellm",
                request_bytes=(request_witness.request_bytes if request_witness else None),
                request_fingerprint=(
                    request_witness.request_fingerprint if request_witness else None
                ),
                request_relation=request_relation,
                common_prefix_items=common_prefix_items,
                previous_request_items=previous_request_items,
                first_divergence_index=first_divergence_index,
                first_divergence_role=first_divergence_role,
                first_divergence_domain=first_divergence_domain,
                first_divergence_previous_domain=first_divergence_previous_domain,
                first_divergence_current_domain=first_divergence_current_domain,
                contributor_mapping_precision=contributor_mapping_precision,
                lost_prefix_items=lost_prefix_items,
                request_witness=request_witness,
                provider_request_id=_response_text_field(response, "request_id"),
                response_id=_response_text_field(response, "id"),
                response_model=_response_text_field(response, "model"),
                system_fingerprint=_response_text_field(response, "system_fingerprint"),
                service_tier=_response_text_field(response, "service_tier"),
                response_cost=response_cost,
                response_cost_source=response_cost_source,
            ),
        )

    def _stream_once(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        diagnostic_callback: Callable[[LLMTransportDiagnostic], None] | None = None,
        diagnostic_attempt: int = 1,
        cancel_check: Callable[[], None] | None = None,
        previous_request_witness: ProviderRequestWitness | None = None,
        transcript_item_domains: tuple[str, ...] | None = None,
        wait_policy: ModelTimeoutPolicy | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        """单次流式调用 LiteLLM（generator function）。

        与 :meth:`_call_once` 同样为多 Provider fallback 预留：未来可在外层
        wrap 多 spec 的容错重试逻辑。

        异常处理涉及两个时机：

        - **初始化时**（``litellm.completion(stream=True)`` 这一行）：典型如
          认证错、bad request
        - **迭代时**（消费 chunk 流时）：典型如中途断网

        事件映射规则：

        - ``chunk.choices[0].delta.content`` 非空 → :class:`LLMTextDelta`
        - ``chunk.choices[0].delta.tool_calls[*]`` → :class:`LLMToolCallDelta`
          （每个数组元素 yield 一次；同一 ``index`` 可能多次到达，调用方累积）
        - 只有隐藏推理或其它不可见增量 → 节流后的 :class:`LLMActivity`
        - 流结束时统一 yield **一次** :class:`LLMTurnDone`，携带标准化
          ``stop_reason`` 与（若 provider 透出的）真实 ``usage``

        Note:
            009 起开启 ``stream_options={"include_usage": True}`` 让 provider 在
            尾 chunk（``choices`` 为空）返回 token 用量。因 usage chunk **晚于**
            带 ``finish_reason`` 的 chunk 到达，故把 ``LLMTurnDone`` 的发射推迟到
            流末——累积 ``stop_reason`` + ``usage`` 后一次性 yield。Provider 不支持
            ``include_usage`` 时 usage 为 ``None``，上层退化到字符估算。
        """
        policy = wait_policy or resolve_timeout_policy(
            spec.defaults,
            overrides,
            streaming=True,
            provider_timeout_is_default=spec.timeout_is_default,
            legacy_stream_seconds=self._stream_timeout_seconds,
        )
        if spec.responses is not None:
            yield from stream_responses(
                spec,
                messages,
                tools=tools,
                overrides=overrides,
                cancel_check=cancel_check,
                diagnostic_callback=diagnostic_callback,
                diagnostic_attempt=diagnostic_attempt,
                timeout_seconds=policy.idle_seconds,
                timeout_policy=policy,
                previous_request_witness=previous_request_witness,
            )
            return
        kwargs: dict[str, Any] = {**spec.defaults, **overrides}
        kwargs["max_retries"] = 0
        kwargs["timeout"] = policy.http_timeout()
        if tools:
            kwargs["tools"] = tools
        # 009：默认请求尾 chunk 带 usage；调用方可经 defaults/overrides 覆盖
        kwargs.setdefault("stream_options", {"include_usage": True})
        request_messages, kwargs = prepare_litellm_request(spec, messages, kwargs)
        prepared_item_domains = (
            transcript_item_domains
            if transcript_item_domains is not None
            and len(transcript_item_domains) == len(request_messages)
            else None
        )
        contributor_options = (
            {"transcript_item_domains": prepared_item_domains}
            if prepared_item_domains is not None
            else {}
        )
        transport = "kimi_direct" if uses_kimi_stream_transport(spec) else "litellm"
        request_witness: ProviderRequestWitness | None = None
        request_bytes: int | None = None
        request_fingerprint: str | None = None
        request_relation = "unavailable"
        common_prefix_items: int | None = None
        previous_request_items = (
            len(previous_request_witness.transcript_item_digests)
            if previous_request_witness is not None
            else None
        )
        first_divergence_index: int | None = None
        first_divergence_role: str | None = None
        first_divergence_domain: str | None = None
        first_divergence_previous_domain: str | None = None
        first_divergence_current_domain: str | None = None
        contributor_mapping_precision = "generic"
        lost_prefix_items: int | None = None
        attempt_started = time.perf_counter()
        _emit_transport_diagnostic(
            diagnostic_callback,
            LLMTransportDiagnostic(
                phase="request_started",
                attempt=diagnostic_attempt,
                attempt_elapsed_ms=0,
                transport=transport,
            ),
        )

        try:
            if cancel_check is not None:
                cancel_check()
            if uses_kimi_stream_transport(spec):
                response = open_cancellable_kimi_stream(
                    spec,
                    request_messages,
                    kwargs,
                    cancel_check or (lambda: None),
                    timeout_policy=policy,
                    **contributor_options,
                )
            else:
                response = open_cancellable_litellm_stream(
                    spec,
                    request_messages,
                    kwargs,
                    cancel_check or (lambda: None),
                    timeout_policy=policy,
                    **contributor_options,
                )
        except BaseException as e:
            if cancel_check is not None:
                cancel_check()
            if not isinstance(e, Exception):
                raise
            failed_witness = getattr(e, "request_witness", None)
            if isinstance(failed_witness, ProviderRequestWitness):
                contributor_mapping_precision = failed_witness.contributor_mapping_precision
                request_witness = failed_witness
                request_bytes = failed_witness.request_bytes
                request_fingerprint = failed_witness.request_fingerprint
                request_relation = failed_witness.relation_to(previous_request_witness)
                common_prefix_items = failed_witness.common_prefix_items(previous_request_witness)
                first_divergence_index = failed_witness.first_divergence_index(
                    previous_request_witness
                )
                first_divergence_role = failed_witness.first_divergence_role(
                    previous_request_witness
                )
                first_divergence_domain = failed_witness.first_divergence_domain(
                    previous_request_witness
                )
                first_divergence_previous_domain = failed_witness.first_divergence_previous_domain(
                    previous_request_witness
                )
                first_divergence_current_domain = failed_witness.first_divergence_current_domain(
                    previous_request_witness
                )
                lost_prefix_items = failed_witness.lost_prefix_items(previous_request_witness)
            mapped_error = _map_exception(e, provider_id=_provider_id_for_spec(spec))
            _emit_transport_diagnostic(
                diagnostic_callback,
                LLMTransportDiagnostic(
                    phase="request_open_failed",
                    attempt=diagnostic_attempt,
                    attempt_elapsed_ms=round((time.perf_counter() - attempt_started) * 1000),
                    transport=transport,
                    request_bytes=request_bytes,
                    request_fingerprint=request_fingerprint,
                    request_relation=request_relation,
                    common_prefix_items=common_prefix_items,
                    first_divergence_index=first_divergence_index,
                    first_divergence_role=first_divergence_role,
                    first_divergence_domain=first_divergence_domain,
                    first_divergence_previous_domain=first_divergence_previous_domain,
                    first_divergence_current_domain=first_divergence_current_domain,
                    contributor_mapping_precision=contributor_mapping_precision,
                    lost_prefix_items=lost_prefix_items,
                    request_witness=request_witness,
                    provider_request_id=getattr(
                        mapped_error,
                        "provider_request_id",
                        None,
                    ),
                    provider_error_type=getattr(
                        mapped_error,
                        "provider_error_type",
                        None,
                    ),
                    status_code=getattr(mapped_error, "status_code", None),
                    retry_after_seconds=getattr(
                        mapped_error,
                        "retry_after_seconds",
                        None,
                    ),
                ),
            )
            raise mapped_error from None
        stream_open_ms = round((time.perf_counter() - attempt_started) * 1000)
        raw_witness = getattr(response, "request_witness", None)
        if isinstance(raw_witness, ProviderRequestWitness):
            request_witness = raw_witness
            contributor_mapping_precision = request_witness.contributor_mapping_precision
            request_bytes = request_witness.request_bytes
            request_fingerprint = request_witness.request_fingerprint
            request_relation = request_witness.relation_to(previous_request_witness)
            common_prefix_items = request_witness.common_prefix_items(previous_request_witness)
            first_divergence_index = request_witness.first_divergence_index(
                previous_request_witness
            )
            first_divergence_role = request_witness.first_divergence_role(previous_request_witness)
            first_divergence_domain = request_witness.first_divergence_domain(
                previous_request_witness
            )
            first_divergence_previous_domain = request_witness.first_divergence_previous_domain(
                previous_request_witness
            )
            first_divergence_current_domain = request_witness.first_divergence_current_domain(
                previous_request_witness
            )
            lost_prefix_items = request_witness.lost_prefix_items(previous_request_witness)
        provider_request_id = _response_text_field(response, "request_id")
        _emit_transport_diagnostic(
            diagnostic_callback,
            LLMTransportDiagnostic(
                phase="stream_opened",
                attempt=diagnostic_attempt,
                attempt_elapsed_ms=stream_open_ms,
                transport=transport,
                request_bytes=request_bytes,
                request_fingerprint=request_fingerprint,
                request_relation=request_relation,
                common_prefix_items=common_prefix_items,
                previous_request_items=previous_request_items,
                first_divergence_index=first_divergence_index,
                first_divergence_role=first_divergence_role,
                first_divergence_domain=first_divergence_domain,
                first_divergence_previous_domain=first_divergence_previous_domain,
                first_divergence_current_domain=first_divergence_current_domain,
                contributor_mapping_precision=contributor_mapping_precision,
                lost_prefix_items=lost_prefix_items,
                request_witness=request_witness,
                provider_request_id=provider_request_id,
            ),
        )

        stop_reason = "end_turn"
        usage: LLMUsage | None = None
        chunk_count = 0
        choice_chunk_count = 0
        usage_only_chunk_count = 0
        text_delta_count = 0
        text_chars = 0
        tool_call_delta_count = 0
        reasoning_delta_count = 0
        reasoning_chars = 0
        reasoning_content_parts: list[str] = []
        reasoning_details: list[dict[str, Any]] = []
        content_parts: list[str] = []
        completed_tool_calls: dict[int, dict[str, str]] = {}
        finish_reason_raw = ""
        delta_fields: set[str] = set()
        first_chunk_ms: int | None = None
        first_reasoning_delta_ms: int | None = None
        last_hidden_activity_emit_at: float | None = None
        response_id: str | None = None
        response_model: str | None = None
        system_fingerprint: str | None = None
        service_tier: str | None = None
        try:
            for chunk in response:
                if cancel_check is not None:
                    cancel_check()
                _raise_stream_chunk_error(chunk)
                chunk_count += 1
                response_id = response_id or _response_text_field(chunk, "id")
                response_model = response_model or _response_text_field(chunk, "model")
                system_fingerprint = system_fingerprint or _response_text_field(
                    chunk, "system_fingerprint"
                )
                service_tier = service_tier or _response_text_field(chunk, "service_tier")
                if first_chunk_ms is None:
                    first_chunk_ms = round((time.perf_counter() - attempt_started) * 1000)
                    _emit_transport_diagnostic(
                        diagnostic_callback,
                        LLMTransportDiagnostic(
                            phase="first_chunk",
                            attempt=diagnostic_attempt,
                            attempt_elapsed_ms=first_chunk_ms,
                            transport=transport,
                            request_bytes=request_bytes,
                            request_fingerprint=request_fingerprint,
                            provider_request_id=provider_request_id,
                            response_id=response_id,
                            response_model=response_model,
                            system_fingerprint=system_fingerprint,
                            service_tier=service_tier,
                        ),
                    )
                chunk_usage = _extract_stream_usage(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage

                if not chunk.choices:
                    usage_only_chunk_count += 1
                    continue  # 尾部 usage-only chunk（choices 为空）等
                choice_chunk_count += 1
                choice = chunk.choices[0]
                delta = choice.delta
                delta_fields.update(_present_delta_field_names(delta))

                content = getattr(delta, "content", None)
                visible_chunk = False
                if content:
                    visible_chunk = True
                    text_delta_count += 1
                    text_chars += len(content)
                    content_parts.append(content)
                    yield LLMTextDelta(text=content)

                reasoning_raw = getattr(delta, "reasoning_content", None)
                if reasoning_raw is None:
                    reasoning_raw = getattr(delta, "reasoning", None)
                reasoning = reasoning_raw if isinstance(reasoning_raw, str) else ""
                detail_chunks = reasoning_detail_objects(getattr(delta, "reasoning_details", None))
                if detail_chunks:
                    reasoning_details.extend(detail_chunks)
                if reasoning:
                    reasoning_content_parts.append(reasoning)
                    reasoning_delta_count += 1
                    reasoning_chars += len(reasoning)
                    if first_reasoning_delta_ms is None:
                        first_reasoning_delta_ms = round(
                            (time.perf_counter() - attempt_started) * 1000
                        )
                        _emit_transport_diagnostic(
                            diagnostic_callback,
                            LLMTransportDiagnostic(
                                phase="first_reasoning_delta",
                                attempt=diagnostic_attempt,
                                attempt_elapsed_ms=first_reasoning_delta_ms,
                                transport=transport,
                                request_bytes=request_bytes,
                                request_fingerprint=request_fingerprint,
                                provider_request_id=provider_request_id,
                                response_id=response_id,
                                response_model=response_model,
                                system_fingerprint=system_fingerprint,
                                service_tier=service_tier,
                            ),
                        )

                tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in tool_calls:
                    visible_chunk = True
                    tool_call_delta_count += 1
                    func = getattr(tc, "function", None)
                    index = getattr(tc, "index", 0) or 0
                    slot = completed_tool_calls.setdefault(
                        index,
                        {"id": "", "name": "", "arguments_json": ""},
                    )
                    raw_id = getattr(tc, "id", "") or ""
                    raw_name = (getattr(func, "name", "") or "") if func else ""
                    raw_arguments = (getattr(func, "arguments", "") or "") if func else ""
                    if raw_id:
                        slot["id"] = raw_id
                    if raw_name:
                        slot["name"] = raw_name
                    slot["arguments_json"] += raw_arguments
                    yield LLMToolCallDelta(
                        index=index,
                        tool_call_id=raw_id,
                        tool_name=raw_name,
                        args_json_delta=raw_arguments,
                    )

                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    finish_reason_raw = str(finish_reason)
                    stop_reason = _map_finish_reason(finish_reason)

                if not visible_chunk and not finish_reason:
                    activity_now = time.monotonic()
                    if (
                        last_hidden_activity_emit_at is None
                        or activity_now - last_hidden_activity_emit_at
                        >= _HIDDEN_ACTIVITY_EMIT_INTERVAL_SECONDS
                    ):
                        last_hidden_activity_emit_at = activity_now
                        yield LLMActivity(
                            kind="reasoning" if reasoning or detail_chunks else "processing"
                        )
        except BaseException as e:
            if cancel_check is not None:
                cancel_check()
            if not isinstance(e, Exception):
                raise
            raise _map_exception(e, provider_id=_provider_id_for_spec(spec)) from None
        finally:
            # 下游 SSE 客户端断开时，上层 generator 会收到 GeneratorExit 并关闭本
            # generator。显式关闭 LiteLLM / provider 的流，避免 HTTP 请求继续在后台
            # 消耗额度、占用 per-session turn lock。
            close = getattr(response, "close", None)
            if callable(close):
                close()

        log_terminal = (
            logger.warning if text_delta_count == 0 and tool_call_delta_count == 0 else logger.info
        )
        log_terminal(
            "[EmptyResponseDbg] llm_stream_terminal model=%s messages=%d tools=%s "
            "chunks=%d choice_chunks=%d usage_only_chunks=%d text_deltas=%d text_chars=%d "
            "tool_call_deltas=%d reasoning_deltas=%d reasoning_chars=%d "
            "delta_fields=%s finish_reason_raw=%s stop_reason=%s usage=%s",
            spec.model,
            len(messages),
            bool(tools),
            chunk_count,
            choice_chunk_count,
            usage_only_chunk_count,
            text_delta_count,
            text_chars,
            tool_call_delta_count,
            reasoning_delta_count,
            reasoning_chars,
            ",".join(sorted(delta_fields)) or "none",
            finish_reason_raw or "none",
            stop_reason,
            usage is not None,
        )

        if text_delta_count == 0 and tool_call_delta_count == 0:
            if stop_reason == "max_tokens" and reasoning_chars > 0:
                raise LLMOutputBudgetExhaustedError(
                    "模型输出预算已被推理内容耗尽，未生成可见正文。"
                )
            raise LLMEmptyResponseError("模型未返回可见正文或工具调用。")

        reasoning_state = build_provider_reasoning_state(
            spec,
            reasoning_content="".join(reasoning_content_parts),
            reasoning_details=reasoning_details,
        )
        assistant_turn = ProviderAssistantTurn(
            content="".join(content_parts) if content_parts else None,
            tool_calls=tuple(
                ProviderToolCall(
                    index=index,
                    tool_call_id=item["id"],
                    tool_name=item["name"],
                    arguments_json=item["arguments_json"],
                )
                for index, item in sorted(completed_tool_calls.items())
            ),
            reasoning_state=reasoning_state,
        )
        response_cost, response_cost_source = _response_cost(response)
        yield LLMTurnDone(
            stop_reason=stop_reason,
            usage=usage,
            reasoning_state=reasoning_state,
            assistant_turn=assistant_turn,
            diagnostics=LLMResponseDiagnostics(
                transport=transport,
                request_bytes=request_bytes,
                request_fingerprint=request_fingerprint,
                request_relation=request_relation,
                common_prefix_items=common_prefix_items,
                previous_request_items=previous_request_items,
                first_divergence_index=first_divergence_index,
                first_divergence_role=first_divergence_role,
                first_divergence_domain=first_divergence_domain,
                first_divergence_previous_domain=first_divergence_previous_domain,
                first_divergence_current_domain=first_divergence_current_domain,
                contributor_mapping_precision=contributor_mapping_precision,
                lost_prefix_items=lost_prefix_items,
                request_witness=request_witness,
                stream_open_ms=stream_open_ms,
                first_chunk_ms=first_chunk_ms,
                first_reasoning_delta_ms=first_reasoning_delta_ms,
                provider_request_id=provider_request_id,
                response_id=response_id,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                service_tier=service_tier,
                response_cost=response_cost,
                response_cost_source=response_cost_source,
            ),
        )
