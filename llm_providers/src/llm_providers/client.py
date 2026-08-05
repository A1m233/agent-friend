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

import json
import logging
import ssl
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cached_property
from typing import Any, cast

import litellm
import openai

from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMContentPolicyError,
    LLMContextWindowError,
    LLMEmptyResponseError,
    LLMError,
    LLMModelMetadataError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTransientProviderError,
)
from .kimi_transport import open_kimi_stream, uses_kimi_stream_transport
from .spec import ProviderSpec
from .stream_events import (
    LLMResponseDiagnostics,
    LLMRetryScheduled,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTransportDiagnostic,
    LLMTurnDone,
    LLMUsage,
)

DEFAULT_NETWORK_RETRIES = 1
"""Provider 尚未产出任何结果时，网络错误的默认重试次数。"""

DEFAULT_NETWORK_RETRY_BACKOFF_SECONDS = 0.5
"""网络重试的初始退避秒数；后续重试按 2 倍递增。"""

DEFAULT_STREAM_TIMEOUT_SECONDS = 45.0
"""聊天流单次等待上游事件的最大秒数；两次总尝试约束在 90 秒内。"""

_TRANSIENT_PROVIDER_ERROR_TYPES = frozenset({"engine_overloaded_error"})
"""Provider 用非 5xx 状态码表达的、可安全有限重试的明确瞬时错误。"""

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedModelLimits:
    """LLMClient 实际采用的模型限制及其证据来源。"""

    context_window: int
    context_window_source: str


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


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    """沿 ``raise ... from ...`` 因果链遍历异常，避免只看到最外层包装。"""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_network_exception(error: Exception) -> bool:
    """识别被 LiteLLM / provider 包装过的连接、TLS 与超时错误。"""
    chain = tuple(_exception_chain(error))
    known_types = (
        openai.APIConnectionError,
        openai.APITimeoutError,
        litellm.APIConnectionError,
        litellm.Timeout,
        litellm.CompletionTimeout,
        ssl.SSLError,
        ConnectionError,
        TimeoutError,
    )
    if any(isinstance(item, known_types) for item in chain):
        return True

    # 部分 provider adapter 会丢掉原始 cause，把 TLS 错误包装成 5xx。
    # 只兜底匹配明确的网络错误特征，避免把普通 InternalServerError 误判为网络错。
    message = " | ".join(str(item) for item in chain)
    return any(
        marker in message
        for marker in (
            "SSL: UNEXPECTED_EOF_WHILE_READING",
            "EOF occurred in violation of protocol",
            "getaddrinfo failed",
        )
    )


def _is_transient_provider_exception(error: Exception) -> bool:
    """识别明确的瞬时 Provider 故障，避免把普通限流或参数错误纳入重试。"""
    for item in _exception_chain(error):
        status_code = getattr(item, "status_code", None)
        if isinstance(status_code, int) and 500 <= status_code <= 599:
            return True
        body = getattr(item, "body", None)
        if not isinstance(body, dict):
            continue
        payload = body.get("error", body)
        if not isinstance(payload, dict):
            continue
        error_type = payload.get("type")
        if (
            status_code == 429
            and isinstance(error_type, str)
            and error_type.casefold() in _TRANSIENT_PROVIDER_ERROR_TYPES
        ):
            return True
    return False


def _extract_usage(response: Any) -> LLMUsage | None:
    """从 litellm 响应对象 / 尾 chunk 抽取 token 用量（009 起）。

    litellm 把 OpenAI 风格的 ``usage`` 透到 ``response.usage``（非流式）或带
    ``stream_options={"include_usage": True}`` 时的尾 chunk 上。Provider 不支持
    时该字段缺失 / 为 ``None`` → 返回 ``None``，上层退化到字符估算。
    """
    return _normalize_usage(_usage_field(response, "usage"), usage_source="top_level")


def _extract_stream_usage(chunk: Any) -> LLMUsage | None:
    """Extract top-level usage or Kimi's provider-specific choice usage."""
    usage = _extract_usage(chunk)
    if usage is not None:
        return usage

    choices = _usage_field(chunk, "choices") or []
    for choice in choices:
        choice_usage = _usage_field(choice, "usage")
        if choice_usage is None:
            model_dump = getattr(choice, "model_dump", None)
            if callable(model_dump):
                dumped = model_dump()
                if isinstance(dumped, dict):
                    choice_usage = dumped.get("usage")
        usage = _normalize_usage(choice_usage, usage_source="choice")
        if usage is not None:
            return usage
    return None


def _normalize_usage(usage: Any, *, usage_source: str | None = None) -> LLMUsage | None:
    """Normalize a provider usage object or mapping into :class:`LLMUsage`."""
    if usage is None:
        return None
    prompt = _usage_field(usage, "prompt_tokens")
    completion = _usage_field(usage, "completion_tokens")
    total = _usage_field(usage, "total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    prompt_tokens = int(prompt or 0)
    cached, cached_source = _first_usage_int_with_source(
        usage,
        "cached_tokens",
        "prompt_cache_hit_tokens",
        "cache_read_input_tokens",
        "_cache_read_input_tokens",
    )
    if cached is None:
        prompt_details = _usage_field(usage, "prompt_tokens_details")
        cached, detail_source = _first_usage_int_with_source(prompt_details, "cached_tokens")
        if detail_source is not None:
            cached_source = f"prompt_tokens_details.{detail_source}"
    cache_miss, cache_miss_source = _first_usage_int_with_source(usage, "prompt_cache_miss_tokens")
    cache_miss_derived = False
    if cache_miss is None and cached is not None:
        cache_miss = max(0, prompt_tokens - cached)
        cache_miss_source = "derived_prompt_minus_cached"
        cache_miss_derived = True
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=int(completion or 0),
        total_tokens=int(total or 0),
        cached_prompt_tokens=cached,
        cache_miss_prompt_tokens=cache_miss,
        usage_source=usage_source,
        cached_prompt_tokens_source=cached_source,
        cache_miss_prompt_tokens_source=cache_miss_source,
        cache_miss_prompt_tokens_derived=cache_miss_derived,
    )


def _first_usage_int_with_source(value: Any, *names: str) -> tuple[int | None, str | None]:
    """Return the first non-negative provider counter and its source field."""
    if value is None:
        return None, None
    for name in names:
        raw = _usage_field(value, name)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and raw >= 0:
            return int(raw), name
    return None, None


def _usage_field(value: Any, name: str) -> Any:
    """Read a usage field from provider objects and raw dictionaries alike."""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _response_text_field(value: Any, name: str) -> str | None:
    raw = _usage_field(value, name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _request_size_bytes(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    kwargs: dict[str, Any],
) -> int:
    """Estimate the model request JSON size without retaining or logging its content."""
    payload: dict[str, Any] = {
        "model": spec.model.removeprefix("openai/"),
        "messages": messages,
        "stream": True,
    }
    for key in (
        "extra_body",
        "max_tokens",
        "response_format",
        "stream_options",
        "temperature",
        "tool_choice",
        "tools",
        "top_p",
    ):
        if key in kwargs:
            payload[key] = kwargs[key]
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return len(rendered.encode("utf-8"))


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


def _map_exception(e: Exception) -> LLMError:
    """把 LiteLLM / openai 的底层异常映射成本项目的 ``LLMError`` 子类。

    在 :meth:`LLMClient.complete` / :meth:`LLMClient.stream` 内统一调用，
    确保上层（``Conversation`` / CLI）只需 ``catch LLMError`` 体系，
    不需要 import LiteLLM 或 openai。
    """
    chain = tuple(_exception_chain(e))
    if any(isinstance(item, openai.AuthenticationError) for item in chain):
        return LLMAuthError(str(e))
    if any(isinstance(item, openai.RateLimitError) for item in chain):
        if _is_transient_provider_exception(e):
            return LLMTransientProviderError(str(e))
        return LLMRateLimitError(str(e))
    if _is_network_exception(e):
        return LLMNetworkError(str(e))
    if _is_content_policy_exception(e):
        return LLMContentPolicyError(str(e))
    if _is_context_window_exception(e):
        return LLMContextWindowError(str(e))
    if any(isinstance(item, openai.BadRequestError) for item in chain):
        return LLMBadRequestError(str(e))
    if _is_transient_provider_exception(e):
        return LLMTransientProviderError(str(e))
    if isinstance(e, openai.APIError):
        return LLMProviderError(str(e))
    return LLMProviderError(f"未预料的错误: {e}")


_CONTEXT_WINDOW_ERROR_CODES = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "input_too_long",
        "prompt_too_long",
    }
)


def _is_context_window_exception(e: Exception) -> bool:
    """识别明确的上下文窗口错误，不把普通 bad request 纳入恢复重试。"""
    for item in _exception_chain(e):
        if isinstance(item, litellm.ContextWindowExceededError):
            return True
        code = getattr(item, "code", None)
        if isinstance(code, str) and code.casefold() in _CONTEXT_WINDOW_ERROR_CODES:
            return True
        body = getattr(item, "body", None)
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                body_code = error.get("code")
                if (
                    isinstance(body_code, str)
                    and body_code.casefold() in _CONTEXT_WINDOW_ERROR_CODES
                ):
                    return True
    return False


def _is_content_policy_exception(e: Exception) -> bool:
    """识别 Provider 明确返回的内容安全拒绝，不把它误报成参数错误。"""
    if any(isinstance(item, litellm.ContentPolicyViolationError) for item in _exception_chain(e)):
        return True
    markers = (
        "content exists risk",
        "content_policy_violation",
        "content policy violation",
        "content_filter",
    )
    return any(marker in str(item).casefold() for item in _exception_chain(e) for marker in markers)


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
        stream_timeout_seconds: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
    ):
        if network_retries < 0:
            raise ValueError("network_retries 不能小于 0")
        if network_retry_backoff_seconds < 0:
            raise ValueError("network_retry_backoff_seconds 不能小于 0")
        if stream_timeout_seconds <= 0:
            raise ValueError("stream_timeout_seconds 必须大于 0")
        self.spec = spec
        self._network_retries = network_retries
        self._network_retry_backoff_seconds = network_retry_backoff_seconds
        self._stream_timeout_seconds = stream_timeout_seconds

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
        for attempt in range(self._network_retries + 1):
            try:
                return self._call_once(self.spec, messages, **overrides)
            except (LLMNetworkError, LLMTransientProviderError):
                if attempt >= self._network_retries:
                    raise
                self._wait_before_network_retry(attempt)
        raise AssertionError("transient retry loop should always return or raise")

    def stream(
        self,
        messages: list[dict[str, Any]],
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
        diagnostic_callback = cast(
            Callable[[LLMTransportDiagnostic], None] | None,
            overrides.pop("_diagnostic_callback", None),
        )
        return self._stream_with_network_retries(
            self.spec,
            messages,
            tools=tools,
            diagnostic_callback=diagnostic_callback,
            **overrides,
        )

    def _stream_with_network_retries(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        diagnostic_callback: Callable[[LLMTransportDiagnostic], None] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        """仅在上游尚未产出任何事件时重试临时故障，避免重复文本或工具调用。"""
        for attempt in range(self._network_retries + 1):
            emitted = False
            should_retry = False
            upstream = self._stream_once(
                spec,
                messages,
                tools=tools,
                diagnostic_callback=diagnostic_callback,
                diagnostic_attempt=attempt + 1,
                **overrides,
            )
            try:
                for event in upstream:
                    emitted = True
                    yield event
                return
            except (LLMNetworkError, LLMTransientProviderError):
                if emitted or attempt >= self._network_retries:
                    raise
                should_retry = True
            finally:
                close = getattr(upstream, "close", None)
                if callable(close):
                    close()
            if should_retry:
                delay = self._network_retry_delay(attempt)
                yield LLMRetryScheduled(
                    attempt=attempt + 2,
                    max_attempts=self._network_retries + 1,
                    delay_seconds=delay,
                )
                self._wait_before_network_retry(attempt)

    def _network_retry_delay(self, attempt: int) -> float:
        return self._network_retry_backoff_seconds * (2**attempt)

    def _wait_before_network_retry(self, attempt: int) -> None:
        delay = self._network_retry_delay(attempt)
        logger.warning(
            "LLM transient request failed before first event; retrying model=%s retry=%d/%d "
            "delay_seconds=%.3f",
            self.spec.model,
            attempt + 1,
            self._network_retries,
            delay,
        )
        if delay > 0:
            time.sleep(delay)

    def _call_once(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        **overrides: Any,
    ) -> str:
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
        kwargs.setdefault("max_retries", 0)

        try:
            response = litellm.completion(
                model=spec.model,
                api_key=spec.api_key,
                api_base=spec.api_base,
                messages=messages,
                **kwargs,
            )
        except Exception as e:
            raise _map_exception(e) from e

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("LLM 返回了空 content。")
        return content

    def _stream_once(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        diagnostic_callback: Callable[[LLMTransportDiagnostic], None] | None = None,
        diagnostic_attempt: int = 1,
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
        - 流结束时统一 yield **一次** :class:`LLMTurnDone`，携带标准化
          ``stop_reason`` 与（若 provider 透出的）真实 ``usage``

        Note:
            009 起开启 ``stream_options={"include_usage": True}`` 让 provider 在
            尾 chunk（``choices`` 为空）返回 token 用量。因 usage chunk **晚于**
            带 ``finish_reason`` 的 chunk 到达，故把 ``LLMTurnDone`` 的发射推迟到
            流末——累积 ``stop_reason`` + ``usage`` 后一次性 yield。Provider 不支持
            ``include_usage`` 时 usage 为 ``None``，上层退化到字符估算。
        """
        kwargs: dict[str, Any] = {**spec.defaults, **overrides}
        kwargs.setdefault("max_retries", 0)
        configured_timeout = kwargs.get("timeout")
        if isinstance(configured_timeout, (int, float)):
            kwargs["timeout"] = min(float(configured_timeout), self._stream_timeout_seconds)
        elif configured_timeout is None:
            kwargs["timeout"] = self._stream_timeout_seconds
        if tools:
            kwargs["tools"] = tools
        # 009：默认请求尾 chunk 带 usage；调用方可经 defaults/overrides 覆盖
        kwargs.setdefault("stream_options", {"include_usage": True})
        transport = "kimi_direct" if uses_kimi_stream_transport(spec) else "litellm"
        request_bytes = _request_size_bytes(spec, messages, kwargs)
        attempt_started = time.perf_counter()
        _emit_transport_diagnostic(
            diagnostic_callback,
            LLMTransportDiagnostic(
                phase="request_started",
                attempt=diagnostic_attempt,
                attempt_elapsed_ms=0,
                transport=transport,
                request_bytes=request_bytes,
            ),
        )

        try:
            if uses_kimi_stream_transport(spec):
                response = open_kimi_stream(spec, messages, kwargs)
            else:
                response = litellm.completion(
                    model=spec.model,
                    api_key=spec.api_key,
                    api_base=spec.api_base,
                    messages=messages,
                    stream=True,
                    **kwargs,
                )
        except Exception as e:
            _emit_transport_diagnostic(
                diagnostic_callback,
                LLMTransportDiagnostic(
                    phase="request_open_failed",
                    attempt=diagnostic_attempt,
                    attempt_elapsed_ms=round((time.perf_counter() - attempt_started) * 1000),
                    transport=transport,
                    request_bytes=request_bytes,
                ),
            )
            raise _map_exception(e) from e
        stream_open_ms = round((time.perf_counter() - attempt_started) * 1000)
        provider_request_id = _response_text_field(response, "request_id")
        _emit_transport_diagnostic(
            diagnostic_callback,
            LLMTransportDiagnostic(
                phase="stream_opened",
                attempt=diagnostic_attempt,
                attempt_elapsed_ms=stream_open_ms,
                transport=transport,
                request_bytes=request_bytes,
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
        finish_reason_raw = ""
        delta_fields: set[str] = set()
        first_chunk_ms: int | None = None
        first_reasoning_delta_ms: int | None = None
        response_id: str | None = None
        response_model: str | None = None
        system_fingerprint: str | None = None
        service_tier: str | None = None
        try:
            for chunk in response:
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
                if content:
                    text_delta_count += 1
                    text_chars += len(content)
                    yield LLMTextDelta(text=content)

                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning is None:
                    reasoning = getattr(delta, "reasoning", None)
                if reasoning:
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
                                provider_request_id=provider_request_id,
                                response_id=response_id,
                                response_model=response_model,
                                system_fingerprint=system_fingerprint,
                                service_tier=service_tier,
                            ),
                        )

                tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in tool_calls:
                    tool_call_delta_count += 1
                    func = getattr(tc, "function", None)
                    yield LLMToolCallDelta(
                        index=getattr(tc, "index", 0) or 0,
                        tool_call_id=getattr(tc, "id", "") or "",
                        tool_name=(getattr(func, "name", "") or "") if func else "",
                        args_json_delta=(getattr(func, "arguments", "") or "") if func else "",
                    )

                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    finish_reason_raw = str(finish_reason)
                    stop_reason = _map_finish_reason(finish_reason)
        except Exception as e:
            raise _map_exception(e) from e
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

        yield LLMTurnDone(
            stop_reason=stop_reason,
            usage=usage,
            diagnostics=LLMResponseDiagnostics(
                transport=transport,
                request_bytes=request_bytes,
                stream_open_ms=stream_open_ms,
                first_chunk_ms=first_chunk_ms,
                first_reasoning_delta_ms=first_reasoning_delta_ms,
                provider_request_id=provider_request_id,
                response_id=response_id,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                service_tier=service_tier,
            ),
        )
