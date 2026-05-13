"""``Conversation``：阶段 1 对话编排的核心类。

负责把 :class:`PromptBuilder` / :class:`ContextManager` / :class:`LLMClient` /
:class:`Memory` 串起来：

::

    PromptBuilder.build()  →  system_prompt
                                    ↓
    Memory.retrieve(user)  →  extra_context (可选)
                                    ↓
    ContextManager.build_messages(history, system, user, extra)
                                    ↓
    LLMClient.complete(...) / .stream(...)
                                    ↓
    history append (user, assistant)

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.2。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

from llm_providers import LLMClient
from memory import Memory

from .context import ContextManager
from .messages import Message
from .prompts import PromptBuilder

DUMP_VERSION = 1


class Conversation:
    """阶段 1 的对话编排核心。

    Args:
        llm_client: 已初始化的 :class:`llm_providers.LLMClient`。
        context_manager: 上下文管理策略，如 :class:`agent.NaiveContextManager`。
        prompt_builder: 人设构建器，如 :class:`agent.MarkdownPromptBuilder`。
            **构造时立刻 build 一次**并缓存，后续不重复 build；如需切换人设
            请新建 :class:`Conversation` 实例。
        memory: 可选的 :class:`memory.Memory` 实现，阶段 1 不传（占位）。

    Note:
        本类**不持久化**。``dump`` / ``load`` 提供序列化接口，是否真的写盘
        由调用方（CLI / 后续 HTTP 层）决定。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        context_manager: ContextManager,
        prompt_builder: PromptBuilder,
        memory: Memory | None = None,
    ):
        self._llm_client = llm_client
        self._context_manager = context_manager
        self._prompt_builder = prompt_builder
        self._memory = memory
        self._system_prompt = prompt_builder.build()
        self._history: list[Message] = []

    @property
    def history(self) -> list[Message]:
        """对话历史的只读视图（返回 copy，外部修改不影响内部状态）。"""
        return list(self._history)

    def reset(self) -> None:
        """清空历史，但保留 system_prompt 和所有依赖。"""
        self._history.clear()

    def send(self, user_input: str) -> str:
        """同步发送一轮对话，返回完整回复。

        Args:
            user_input: 用户输入文本。

        Returns:
            assistant 的完整回复字符串。

        Raises:
            llm_providers.LLMError: 任意子类，调用方负责抹平和提示。

        Note:
            **失败时 history 不会更新**——若 LLM 调用抛异常，本轮的 user
            消息不会进 history，下次重试是干净的。
        """
        openai_messages = self._build_openai_messages(user_input)
        reply = self._llm_client.complete(openai_messages)

        self._history.append(Message(role="user", content=user_input))
        self._history.append(Message(role="assistant", content=reply))
        return reply

    def stream(self, user_input: str) -> Iterator[str]:
        """流式发送一轮对话，逐 chunk yield 回复。

        典型用法（CLI 边收边打印）::

            for chunk in conv.stream(user_input):
                print(chunk, end="", flush=True)

        Args:
            user_input: 用户输入文本。

        Yields:
            assistant 文本片段。

        Raises:
            llm_providers.LLMError: 任意子类。**初始化阶段抛**（如认证错）则
            history 完全不变；**迭代中途抛**（如断网）则 history 会记录已收到
            的部分回复，``meta["partial"] = True`` 标识不完整。

        Note:
            ``KeyboardInterrupt`` 等 :class:`BaseException` 也会被识别为部分回复
            （通过 :func:`sys.exc_info`）。
        """
        openai_messages = self._build_openai_messages(user_input)

        buffer: list[str] = []
        try:
            for chunk in self._llm_client.stream(openai_messages):
                buffer.append(chunk)
                yield chunk
        finally:
            exc_info = sys.exc_info()
            interrupted = exc_info[0] is not None
            if interrupted and not buffer:
                # 一个 chunk 都没收到（典型：初始化阶段就异常）：history 不动
                pass
            else:
                self._history.append(Message(role="user", content=user_input))
                meta: dict[str, Any] = {"partial": True} if interrupted else {}
                self._history.append(
                    Message(
                        role="assistant",
                        content="".join(buffer),
                        meta=meta,
                    )
                )

    def dump(self) -> dict[str, Any]:
        """序列化对话状态为 dict。

        **只存 history**，不存 system_prompt / persona / 依赖配置——这些由调用方
        在 :meth:`load` 时重新提供。这样 dump 文件可以跨人设切换、跨 model
        切换复用历史。

        Returns:
            形如 ``{"version": 1, "history": [...]}`` 的可 JSON 序列化的 dict。
        """
        return {
            "version": DUMP_VERSION,
            "history": [m.to_dict() for m in self._history],
        }

    @classmethod
    def load(
        cls,
        data: dict[str, Any],
        llm_client: LLMClient,
        context_manager: ContextManager,
        prompt_builder: PromptBuilder,
        memory: Memory | None = None,
    ) -> Conversation:
        """从 :meth:`dump` 的输出恢复一个 :class:`Conversation` 实例。

        Args:
            data: :meth:`dump` 的输出。
            llm_client: 同 :meth:`__init__`，由调用方重新构造。
            context_manager: 同上。
            prompt_builder: 同上。可换成不同人设。
            memory: 同上。

        Returns:
            包含历史的新实例。

        Raises:
            ValueError: ``data["version"]`` 不被本版本支持。
        """
        version = data.get("version")
        if version != DUMP_VERSION:
            raise ValueError(f"不支持的 dump 版本: {version}（当前支持 {DUMP_VERSION}）")
        conv = cls(
            llm_client=llm_client,
            context_manager=context_manager,
            prompt_builder=prompt_builder,
            memory=memory,
        )
        conv._history = [Message.from_dict(d) for d in data["history"]]
        return conv

    def _build_openai_messages(self, user_input: str) -> list[dict[str, str]]:
        """组装本轮要发给 LLM 的消息列表（OpenAI 风格的 dict）。"""
        extra = self._memory.retrieve(user_input) if self._memory else None
        build = self._context_manager.build_messages(
            history=self._history,
            system_prompt=self._system_prompt,
            new_user_input=user_input,
            extra_context=extra,
        )
        return [m.to_openai() for m in build.messages]
