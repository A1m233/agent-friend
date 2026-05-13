"""agent-friend · 记忆系统模块。

阶段 1 仅占位：定义 :class:`Memory` Protocol 的最小签名，
让 ``agent.Conversation`` 可以预留 ``memory: Memory | None`` 参数。

阶段 2 会单独写实现（基于 SQLite + 可选向量检索），届时本模块会扩展，
但 :meth:`Memory.retrieve` 的核心签名只能向后兼容（加可选参数），
不能改已有签名。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.6。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent.messages import Message

__version__ = "0.1.0"


class Memory(Protocol):
    """记忆系统的接口契约（阶段 1 仅占位）。

    Note:
        循环依赖处理：本模块在运行时**不 import** ``agent.messages``，
        通过 ``TYPE_CHECKING`` + ``from __future__ import annotations`` 让
        ``Message`` 类型注解只在静态类型检查时被解析。
    """

    def retrieve(self, query: str) -> list[Message]:
        """检索与 ``query`` 相关的历史消息。

        Args:
            query: 当前用户输入或检索关键词。

        Returns:
            按相关度排序的历史消息列表，由 ``Conversation`` 作为
            ``extra_context`` 注入到 ``ContextManager.build_messages``。

        Note:
            阶段 2 会向后兼容地扩展此方法（如加 ``top_k`` / ``filters`` 等
            可选参数），但已有签名不变。
        """
        ...


__all__ = ["Memory"]
