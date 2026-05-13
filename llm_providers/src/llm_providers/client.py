"""``LLMClient``：对 LiteLLM 的最小封装，对外暴露统一接口 + 抹平错误。

提供两个公开方法：

- :meth:`LLMClient.complete` — 同步一次性返回完整回复
- :meth:`LLMClient.stream`   — 流式返回 token chunk（generator 风格）

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.2。
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


class LLMClient:
    """统一的 LLM 调用客户端。

    Args:
        spec: ``ProviderSpec`` 实例，决定调用哪个 Provider、用什么 model、
            用什么 key。
    """

    def __init__(self, spec: ProviderSpec):
        self.spec = spec

    def complete(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        """同步调用 LLM，返回完整的 assistant 文本回复。

        Args:
            messages: OpenAI 风格的消息列表，每条形如
                ``{"role": "user" | "system" | "assistant", "content": "..."}``。
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
        """
        return self._call_once(self.spec, messages, **overrides)

    def stream(self, messages: list[dict[str, str]], **overrides: Any) -> Iterator[str]:
        """流式调用 LLM，逐个返回 token chunk（generator 风格）。

        典型用法（CLI 边收边打印）::

            buffer = []
            for chunk in client.stream(messages):
                print(chunk, end="", flush=True)
                buffer.append(chunk)
            full_reply = "".join(buffer)

        Args:
            messages: 同 :meth:`complete`。
            **overrides: 同 :meth:`complete`。

        Yields:
            assistant 文本片段（token 拼装得到的小块字符串，可能为单字到几字）。

        Raises:
            同 :meth:`complete`，但异常可能在初始化或迭代中任一时机抛出。
        """
        return self._stream_once(self.spec, messages, **overrides)

    def _call_once(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, str]],
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
        messages: list[dict[str, str]],
        **overrides: Any,
    ) -> Iterator[str]:
        """单次流式调用 LiteLLM（generator function）。

        与 :meth:`_call_once` 同样为多 Provider fallback 预留：未来可在外层
        wrap 多 spec 的容错重试逻辑。

        异常处理涉及两个时机：

        - **初始化时**（``litellm.completion(stream=True)`` 这一行）：典型如
          认证错、bad request
        - **迭代时**（消费 chunk 流时）：典型如中途断网
        """
        kwargs: dict[str, Any] = {**spec.defaults, **overrides}

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

        try:
            for chunk in response:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            raise _map_exception(e) from e
