"""``LLMClient``：对 LiteLLM 的最小封装，对外暴露统一接口 + 抹平错误。

提供两个公开方法：

- :meth:`LLMClient.complete` — 同步一次性返回完整回复
- :meth:`LLMClient.stream`   — 流式返回事件序列（generator 风格）

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.2 与
docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.5。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import litellm
import openai

from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)
from .spec import ProviderSpec
from .stream_events import (
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
    if isinstance(e, openai.AuthenticationError):
        return LLMAuthError(str(e))
    if isinstance(e, openai.RateLimitError):
        return LLMRateLimitError(str(e))
    if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
        return LLMNetworkError(str(e))
    if isinstance(e, (litellm.ContextWindowExceededError, openai.BadRequestError)):
        return LLMBadRequestError(str(e))
    if isinstance(e, openai.APIError):
        return LLMProviderError(str(e))
    return LLMProviderError(f"未预料的错误: {e}")


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

    def __init__(self, spec: ProviderSpec):
        self.spec = spec

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
        return self._call_once(self.spec, messages, **overrides)

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
        return self._stream_once(self.spec, messages, tools=tools, **overrides)

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
        try:
            for chunk in response:
                chunk_usage = _extract_usage(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage

                if not chunk.choices:
                    continue  # 尾部 usage-only chunk（choices 为空）等
                choice = chunk.choices[0]
                delta = choice.delta

                content = getattr(delta, "content", None)
                if content:
                    yield LLMTextDelta(text=content)

                tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in tool_calls:
                    func = getattr(tc, "function", None)
                    yield LLMToolCallDelta(
                        index=getattr(tc, "index", 0) or 0,
                        tool_call_id=getattr(tc, "id", "") or "",
                        tool_name=(getattr(func, "name", "") or "") if func else "",
                        args_json_delta=(getattr(func, "arguments", "") or "") if func else "",
                    )

                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    stop_reason = _map_finish_reason(finish_reason)
        except Exception as e:
            raise _map_exception(e) from e

        yield LLMTurnDone(stop_reason=stop_reason, usage=usage)
