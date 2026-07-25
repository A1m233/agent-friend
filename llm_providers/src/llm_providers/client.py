"""``LLMClient``：对 LiteLLM 的最小封装，对外暴露统一接口 + 抹平错误。

提供两个公开方法：

- :meth:`LLMClient.complete` — 同步一次性返回完整回复
- :meth:`LLMClient.stream`   — 流式返回事件序列（generator 风格）

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.2 与
docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.5。
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Iterator
from typing import Any

import litellm
import openai

from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMContentPolicyError,
    LLMContextWindowError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTransientProviderError,
)
from .spec import ProviderSpec
from .stream_events import (
    LLMRetryScheduled,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
)

DEFAULT_CONTEXT_WINDOW = 8192
"""litellm 查不到、spec 也没 override 时的保守兜底窗口（009 起）。

取小不取大：窗口估小 → 阈值偏低 → 上下文管理偏早触发裁剪/压缩，安全（宁可早
压缩，不可漏压缩撑爆）。详见 009 design §3.2。
"""

DEFAULT_NETWORK_RETRIES = 1
"""Provider 尚未产出任何结果时，网络错误的默认重试次数。"""

DEFAULT_NETWORK_RETRY_BACKOFF_SECONDS = 0.5
"""网络重试的初始退避秒数；后续重试按 2 倍递增。"""

DEFAULT_STREAM_TIMEOUT_SECONDS = 45.0
"""聊天流单次等待上游事件的最大秒数；两次总尝试约束在 90 秒内。"""

logger = logging.getLogger(__name__)


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
    """识别明确的 Provider 5xx，避免把认证、限流或参数错误纳入重试。"""
    for item in _exception_chain(error):
        status_code = getattr(item, "status_code", None)
        if isinstance(status_code, int) and 500 <= status_code <= 599:
            return True
    return False


def _extract_usage(response: Any) -> LLMUsage | None:
    """从 litellm 响应对象 / 尾 chunk 抽取 token 用量（009 起）。

    litellm 把 OpenAI 风格的 ``usage`` 透到 ``response.usage``（非流式）或带
    ``stream_options={"include_usage": True}`` 时的尾 chunk 上。Provider 不支持
    时该字段缺失 / 为 ``None`` → 返回 ``None``，上层退化到字符估算。
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is None and completion is None and total is None:
        return None
    return LLMUsage(
        prompt_tokens=int(prompt or 0),
        completion_tokens=int(completion or 0),
        total_tokens=int(total or 0),
    )


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
        """当前 model 的最大输入 token 窗口（009 起；三层兜底）。

        解析顺序（009 design §3.2）：

        1. :attr:`ProviderSpec.context_window` override（私有 ``api_base`` /
           litellm 不认识的 model 时显式指定）
        2. ``litellm.get_model_info(model)`` 的 ``max_input_tokens`` /
           ``max_tokens`` 元数据
        3. :data:`DEFAULT_CONTEXT_WINDOW` 保守兜底（取小，偏早触发上下文管理）

        Note:
            把 litellm 细节收口在本层，让 ``agent`` 侧的 budget 逻辑只依赖本属性、
            **不直接 import litellm**，保持现有分层隔离。
        """
        if self.spec.context_window is not None:
            return self.spec.context_window
        try:
            info = litellm.get_model_info(self.spec.model)
        except Exception:
            return DEFAULT_CONTEXT_WINDOW
        if isinstance(info, dict):
            win = info.get("max_input_tokens") or info.get("max_tokens")
            if win:
                try:
                    return int(win)
                except (ValueError, TypeError):
                    pass
        return DEFAULT_CONTEXT_WINDOW

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
        return self._stream_with_network_retries(
            self.spec,
            messages,
            tools=tools,
            **overrides,
        )

    def _stream_with_network_retries(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        """仅在上游尚未产出任何事件时重试临时故障，避免重复文本或工具调用。"""
        for attempt in range(self._network_retries + 1):
            emitted = False
            should_retry = False
            upstream = self._stream_once(spec, messages, tools=tools, **overrides)
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

        try:
            response = litellm.completion(
                model=spec.model,
                api_key=spec.api_key,
                api_base=spec.api_base,
                messages=messages,
                stream=True,
                **kwargs,
            )
        except Exception as e:
            raise _map_exception(e) from e

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
        try:
            for chunk in response:
                chunk_count += 1
                chunk_usage = _extract_usage(chunk)
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

        yield LLMTurnDone(stop_reason=stop_reason, usage=usage)
