"""上下文管理器：决定每次调 LLM 时实际发什么 ``messages``。

阶段 1 提供最朴素的 :class:`NaiveContextManager`（全发不截断）。未来可加
``FifoContextManager`` / ``TokenBudgetContextManager`` / ``SummarizingContextManager``
等不同策略，``Conversation`` 通过构造注入即可切换。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .messages import Message


@dataclass
class BuildResult:
    """:meth:`ContextManager.build_messages` 的结构化返回值。

    Attributes:
        messages: 实际要发给 LLM 的消息序列（``system`` 在最前、最新 ``user``
            在最后）。
        dropped_count: 截掉的历史消息条数。:class:`NaiveContextManager` 永远是 0。
        notes: 扩展用，未来放 ``token_estimate`` 等。
    """

    messages: list[Message]
    dropped_count: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


class ContextManager(Protocol):
    """上下文管理器的接口契约。

    所有实现都必须遵循以下不变量：

    1. ``system_prompt`` 不为空时，必须放在 ``BuildResult.messages`` 的最前
    2. ``new_user_input`` 必须放在 ``BuildResult.messages`` 的最后
    3. ``extra_context``（阶段 2 的 memory 注入）若提供，应放在 ``system`` 之后、
       ``history`` 之前
    4. ``history`` 可能被截断，截断的条数记入 ``BuildResult.dropped_count``
    """

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str,
        extra_context: list[Message] | None = None,
    ) -> BuildResult: ...


class NaiveContextManager:
    """阶段 1 实现：全发不截断。

    简单拼接 ``[system?] + extra_context? + history + [new_user]``，不做 token 预检
    （计算 token 也要调 tokenizer，成本高，YAGNI）。

    若 history 真的爆了 LLM 的 context window，调用层会收到
    :class:`llm_providers.LLMBadRequestError`，由 CLI 给出友好提示。
    """

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str,
        extra_context: list[Message] | None = None,
    ) -> BuildResult:
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        if extra_context:
            messages.extend(extra_context)
        messages.extend(history)
        messages.append(Message(role="user", content=new_user_input))
        return BuildResult(messages=messages)
