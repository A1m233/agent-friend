"""``LLMClient``：对 LiteLLM 的最小封装，对外暴露统一接口 + 抹平错误。

M0.1 范围：仅 ``complete`` 同步接口；``stream`` 留到 M0.2。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.2。
"""

from __future__ import annotations

from typing import Any

import litellm
import openai

from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)
from .spec import ProviderSpec


class LLMClient:
    """统一的 LLM 调用客户端。

    Args:
        spec: ``ProviderSpec`` 实例，决定调用哪个 Provider、用什么 model、
            用什么 key。

    Note:
        M0.1 阶段仅实现 :meth:`complete`（同步）。
        ``stream`` 接口将在 M0.2 加入。
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

    def _call_once(
        self,
        spec: ProviderSpec,
        messages: list[dict[str, str]],
        **overrides: Any,
    ) -> str:
        """单次调用 LiteLLM。

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
        except openai.AuthenticationError as e:
            raise LLMAuthError(str(e)) from e
        except openai.RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise LLMNetworkError(str(e)) from e
        except (
            litellm.ContextWindowExceededError,
            openai.BadRequestError,
        ) as e:
            raise LLMBadRequestError(str(e)) from e
        except openai.APIError as e:
            raise LLMProviderError(str(e)) from e
        except Exception as e:
            raise LLMProviderError(f"未预料的错误: {e}") from e

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("LLM 返回了空 content。")
        return content
