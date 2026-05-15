"""``Conversation``：绑定 :class:`Session` 跑对话的运行时执行器。

负责把 :class:`PromptBuilder` / :class:`ContextManager` / :class:`LLMClient` /
:class:`Memory` 串起来，并在每轮对话/切换时把事件 append 到 :class:`Session`
和 :class:`SessionStore`：

::

    PromptBuilder.build()  →  system_prompt
                                    ↓
    Memory.retrieve(user)  →  extra_context (可选)
                                    ↓
    ContextManager.build_messages(session.messages, system, user, extra)
                                    ↓
    LLMClient.complete(...) / .stream(...)
                                    ↓
    Event(user_message)  +  Event(assistant_message)
                                    ↓
    session.append + store.append_event

详见 docs/requirements/002-engine-session-management/design.md §4.7。

Note:
    本类**不持久化的逻辑**——所有 IO 都委托给注入的 :class:`SessionStore`。
    历史从 :attr:`Session.messages` 派生，没有内部 ``_history`` 副本。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from llm_providers import LLMClient
from memory import Memory

from .context import ContextManager
from .errors import AgentError
from .messages import Message
from .personas import PersonaCatalog
from .prompts import PromptBuilder
from .sessions import Event, Session, SessionStore

if TYPE_CHECKING:
    LLMClientFactory = Callable[[str], LLMClient]
    PromptBuilderFactory = Callable[[str], PromptBuilder]


class Conversation:
    """绑定 :class:`Session` 的对话执行器。

    Args:
        session: 已加载或新建的会话。历史从 ``session.events`` 派生。
        store: 持久化实现，每次 send / stream / switch_* 都会写盘。
        llm_client: 当前激活 model 对应的 :class:`LLMClient`。
        context_manager: 上下文管理策略，如 :class:`agent.NaiveContextManager`。
        prompt_builder: 当前激活 persona 对应的 :class:`PromptBuilder`。
            **构造时立刻 build 一次**并缓存；切换 persona 时会通过工厂重建。
        llm_client_factory: 接 model 名 → :class:`LLMClient`，用于 :meth:`switch_model`。
            未注入时调 :meth:`switch_model` 会抛 :class:`AgentError`。
        prompt_builder_factory: 接 **persona_id** → :class:`PromptBuilder`，用于
            :meth:`switch_persona`。未注入时调 :meth:`switch_persona` 会抛 :class:`AgentError`。
        catalog: 用于 ``switch_persona`` 时通过 id 反查 name 写双字段事件 hint。
            ``None`` 时新建一个默认的（孵化期接受）。
        memory: 可选的 :class:`memory.Memory` 实现，阶段 1 不传（占位）。

    Note:
        典型构造方式是用 :meth:`SessionManager.start_conversation` 装配，
        手工构造仅用于测试。
    """

    def __init__(
        self,
        session: Session,
        store: SessionStore,
        llm_client: LLMClient,
        context_manager: ContextManager,
        prompt_builder: PromptBuilder,
        llm_client_factory: LLMClientFactory | None = None,
        prompt_builder_factory: PromptBuilderFactory | None = None,
        catalog: PersonaCatalog | None = None,
        memory: Memory | None = None,
    ):
        self._session = session
        self._store = store
        self._llm_client = llm_client
        self._context_manager = context_manager
        self._prompt_builder = prompt_builder
        self._llm_client_factory = llm_client_factory
        self._prompt_builder_factory = prompt_builder_factory
        self._catalog = catalog or PersonaCatalog()
        self._memory = memory
        self._system_prompt = prompt_builder.build()

    # ----- 只读视图 -----

    @property
    def session(self) -> Session:
        """绑定的会话实例（只读引用，请勿直接修改 events）。"""
        return self._session

    @property
    def history(self) -> list[Message]:
        """对话历史的只读视图，从 :attr:`Session.messages` 派生。

        每次访问都会遍历事件流（O(n)），孵化期 n < 1000，无性能问题。
        """
        return self._session.messages

    @property
    def current_persona(self) -> str:
        """当前激活 persona 名（从 session 派生）。"""
        return self._session.current_persona

    @property
    def current_model(self) -> str:
        """当前激活 model 名（从 session 派生）。"""
        return self._session.current_model

    # ----- 对话主流程 -----

    def send(self, user_input: str) -> str:
        """同步发送一轮对话，返回完整回复。

        Args:
            user_input: 用户输入文本。

        Returns:
            assistant 的完整回复字符串。

        Raises:
            llm_providers.LLMError: 任意子类。
            SessionPersistError: 事件落盘失败（**LLM 已成功调用、回复已生成**，
                调用方拿到 reply 后落盘才失败——本轮回复**未持久化**）。

        Note:
            **LLM 失败时事件不会落盘**——本轮的 user / assistant 消息都不会进
            session.events，下次重试是干净的。
        """
        persona_name = self._session.current_persona_name
        persona_id = self._session.current_persona_id
        model_snapshot = self._session.current_model
        openai_messages = self._build_openai_messages(user_input)
        reply = self._llm_client.complete(openai_messages)

        self._append_user_event(user_input)
        self._append_assistant_event(
            reply,
            partial=False,
            persona_name=persona_name,
            persona_id=persona_id,
            model=model_snapshot,
        )
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
                事件完全不落盘；**迭代中途抛**（如断网）则会落 user_message +
                ``payload.partial = True`` 的 assistant_message。
            SessionPersistError: 事件落盘失败（同 :meth:`send`）。

        Note:
            ``KeyboardInterrupt`` 等 :class:`BaseException` 也会被识别为部分回复
            （通过 :func:`sys.exc_info`）。
        """
        persona_name = self._session.current_persona_name
        persona_id = self._session.current_persona_id
        model_snapshot = self._session.current_model
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
                pass
            else:
                self._append_user_event(user_input)
                self._append_assistant_event(
                    "".join(buffer),
                    partial=interrupted,
                    persona_name=persona_name,
                    persona_id=persona_id,
                    model=model_snapshot,
                )

    # ----- 切换 persona / model -----

    def switch_persona(self, persona_id: str) -> None:
        """切换 persona：落盘 ``persona_change`` 事件（双字段 id+name）+ 重建 PromptBuilder。

        若新 persona 与当前相同则**无操作直接返回**（不写事件）。

        Args:
            persona_id: 目标 persona 的 UUID。

        Raises:
            AgentError: ``prompt_builder_factory`` 未注入。
            PersonaNotFoundError: 该 id 在 catalog 找不到。**此时事件未落盘**，
                状态完全不变。
            SessionPersistError: 事件落盘失败。**此时 PromptBuilder 已构造但未替换**，
                状态完全不变。

        Note:
            实施顺序：``catalog.get(id)`` 取 name → ``factory(id)`` + 预先 ``build()``
            （客户端预检）→ ``store.append_event`` → ``session.append`` → swap 内部依赖。
        """
        if self._prompt_builder_factory is None:
            raise AgentError(
                "switch_persona 需要 prompt_builder_factory 注入（请通过 SessionManager 装配）"
            )

        from_id = self._session.current_persona_id
        from_name = self._session.current_persona_name
        if from_id == persona_id:
            return

        new_info = self._catalog.get(persona_id)
        new_builder = self._prompt_builder_factory(persona_id)
        new_system_prompt = new_builder.build()

        event = Event(
            type="persona_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "from_id": from_id,
                "from": from_name,
                "to_id": persona_id,
                "to": new_info.name,
            },
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

        self._prompt_builder = new_builder
        self._system_prompt = new_system_prompt

    def switch_model(self, name: str) -> None:
        """切换 model：落盘 ``model_change`` 事件 + 重建 :class:`LLMClient`。

        若新 model 与当前相同则**无操作直接返回**。

        Args:
            name: 目标 model 名（LiteLLM 风格，如 ``"deepseek/deepseek-v4-flash"``）。

        Raises:
            AgentError: ``llm_client_factory`` 未注入。
            LLMAuthError / ValueError: 工厂构造 LLMClient 失败。**事件未落盘**，状态不变。
            SessionPersistError: 事件落盘失败。**LLMClient 已构造但未替换**，状态不变。
        """
        if self._llm_client_factory is None:
            raise AgentError(
                "switch_model 需要 llm_client_factory 注入（请通过 SessionManager 装配）"
            )

        from_model = self._session.current_model
        if from_model == name:
            return

        new_client = self._llm_client_factory(name)

        event = Event(
            type="model_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"from": from_model, "to": name},
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

        self._llm_client = new_client

    # ----- 内部辅助 -----

    def _append_user_event(self, content: str) -> None:
        event = Event(
            type="user_message",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"content": content},
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _append_assistant_event(
        self,
        content: str,
        *,
        partial: bool,
        persona_name: str,
        persona_id: str | None,
        model: str,
    ) -> None:
        meta: dict[str, Any] = {"persona": persona_name, "model": model}
        if persona_id is not None:
            meta["persona_id"] = persona_id
        event = Event(
            type="assistant_message",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"content": content, "partial": partial},
            meta=meta,
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _build_openai_messages(self, user_input: str) -> list[dict[str, str]]:
        """组装本轮要发给 LLM 的消息列表（OpenAI 风格的 dict）。"""
        extra = self._memory.retrieve(user_input) if self._memory else None
        build = self._context_manager.build_messages(
            history=self._session.messages,
            system_prompt=self._system_prompt,
            new_user_input=user_input,
            extra_context=extra,
        )
        return [m.to_openai() for m in build.messages]

    # ----- 调试辅助 -----

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Conversation(session_id={self._session.session_id[:8]}, "
            f"persona={self._session.current_persona!r}, "
            f"model={self._session.current_model!r}, "
            f"messages={len(self._session.messages)})"
        )

    # ----- 兼容性显式签名占位（防止误用旧 API）-----

    def reset(self) -> None:
        """**已删除** —— 002 架构下与 ``/new`` 语义重复。

        Raises:
            AttributeError: 请改用 :meth:`SessionManager.create` 新建会话。
        """
        raise AttributeError(
            "Conversation.reset() 已在 002 移除（与 /new 语义重复）。"
            "请改用 SessionManager.create() 新建会话。"
        )

    def dump(self) -> dict[str, Any]:
        """**已删除** —— 持久化走 :class:`SessionStore`。

        Raises:
            AttributeError: 请改用 :meth:`Session.to_dict`。
        """
        raise AttributeError(
            "Conversation.dump() 已在 002 移除。"
            "持久化走 SessionStore；如需快照导出请用 conv.session.to_dict()。"
        )

    @classmethod
    def load(cls, *args: Any, **kwargs: Any) -> Conversation:
        """**已删除** —— 持久化走 :class:`SessionStore`。

        Raises:
            AttributeError: 请改用 :meth:`SessionManager.open` + :meth:`SessionManager.start_conversation`。
        """
        raise AttributeError(
            "Conversation.load() 已在 002 移除。"
            "请改用 SessionManager.open(session_id) + start_conversation(session)。"
        )
