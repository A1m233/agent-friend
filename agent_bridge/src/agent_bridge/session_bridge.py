"""``SessionBridge`` —— bridge 进程的"会话桥"。

承担三种语义模式的统一调度：

1. **transient（OpenAI 默认无状态）**：客户端在 messages 数组里发完整历史；
   bridge 进程内为这一次请求构造 in-memory :class:`Session` + :class:`Conversation`，
   跑完即丢——磁盘不留任何痕迹。
2. **persistent + auto-create（AG-UI 默认）**：客户端在 body 里发 ``thread_id``；
   bridge 把 ``thread_id`` 直接当 ``session_id``，存在则 ``open``、不存在则
   ``create(session_id=thread_id)``，落盘到 ``agent.paths.sessions_dir()``。
3. **persistent（OpenAI 扩展位，本期不暴露 CLI 入口）**：客户端通过
   ``X-Agent-Friend-Session-Id`` header 显式指定已存在的 session id；
   M6.2 仅暴露 AG-UI 通道，OpenAI 扩展位的接入由后续里程碑决定是否启用。

详见 docs/requirements/006-agent-bridge/design.md §4.4。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent import Conversation, Message, Session, SessionNotFoundError

from .assembly import BridgeRuntime


@dataclass(frozen=True)
class PersistentBootstrap:
    """AG-UI 持久化分支的输入。

    Attributes:
        thread_id: AG-UI ``RunAgentInput.thread_id``；bridge 把它当 ``session_id`` 用。
            不存在时**自动创建**新 session（id 即 thread_id），用 ``default_persona``
            / ``default_model`` 装配。这是 AG-UI 协议"无感"语义的体现，详见
            design §4.4.2。
        new_user_input: 本轮 user 新输入文本。AG-UI ``messages`` 里之前的历史
            **不**被搬到 bridge——bridge 假设 ``thread_id`` 对应的 jsonl 已经
            落盘了之前所有事件，session ``open`` 时会自然 replay 进 in-memory
            事件流。
        default_persona: 自动创建时的默认 persona（与 OpenAI 出口共享同一个默认）。
        default_model: 自动创建时的默认 model。
    """

    thread_id: str
    new_user_input: str
    default_persona: str
    default_model: str


@dataclass(frozen=True)
class TransientBootstrap:
    """OpenAI 无状态分支启动 :class:`Conversation` 所需的全部输入。

    Attributes:
        history: 不含最新一条 user 输入的历史消息（按时间顺序）。``role="system"``
            / ``role="tool"`` 的消息**已在协议解码层过滤**（详见 design §4.3.3），
            到这里只剩 ``user`` / ``assistant``。
        latest_user_input: 本轮 user 的最新输入文本；将作为
            :meth:`Conversation.stream` 的 ``user_input`` 参数。
        persona: persona name（slug）；通常为默认 persona。
        model: 目标 model 名；本期由 :class:`BridgeRuntime` 的默认值兜底。
    """

    history: list[Message]
    latest_user_input: str
    persona: str
    model: str


class SessionBridge:
    """OpenAI / AG-UI 双协议共用的会话装配入口。"""

    def __init__(self, runtime: BridgeRuntime) -> None:
        self._runtime = runtime

    def session_exists(self, session_id: str) -> bool:
        """检查 ``session_id`` 是否已在持久化 store 中存在。"""
        try:
            self._runtime.persistent_session_manager.open(session_id)
        except SessionNotFoundError:
            return False
        return True

    def start_transient(self, boot: TransientBootstrap) -> Conversation:
        """OpenAI 默认无状态分支：用 ``boot.history`` 在内存里造 :class:`Session`，
        再用 transient :class:`SessionManager`（注入 :class:`NullSessionStore`）
        装配 :class:`Conversation`，整轮跑完后整个 session 与所有事件随
        ``Conversation`` 一起被 GC 回收。

        实现笔记：

        - bridge 不知道客户端历史里"创建会话时是什么 persona / model"——本期
          一律按"用 runtime 的默认 persona + 当前请求传入的 model"装配；这等同于
          "把整段历史当作一段独立的、新建会话的对话上下文"
        - ``messages`` 列表的最后一条 user 输入由调用方剥离出来作为
          ``latest_user_input``，剩下的当历史；这跟 ``Conversation.stream``
          的接口语义一致（它要的是"历史 + 新输入"，而不是"已包含新输入的历史"）

        Args:
            boot: 解码完成的 OpenAI 请求中可用的全部信息。

        Returns:
            装配好的 :class:`Conversation`，可立刻调用 ``stream(latest_user_input)``。

        Raises:
            agent.PersonaNotFoundError: ``boot.persona`` 在 catalog 找不到。
            llm_providers.LLMAuthError: 工厂构造 :class:`LLMClient` 失败。
        """
        mgr = self._runtime.make_transient_session_manager()

        persona_info = self._runtime.catalog.find_by_name(boot.persona)

        session = mgr.create(
            persona=persona_info.name,
            model=boot.model,
            persona_id=persona_info.id,
        )

        for msg in boot.history:
            self._append_history_message_in_memory(session, msg)

        return mgr.start_conversation(session)

    def bind_persistent(self, boot: PersistentBootstrap) -> Conversation:
        """AG-UI 持久化分支：``thread_id`` ↔ ``session_id`` 一对一。

        - ``thread_id`` 已存在（jsonl 落盘）→ ``open(thread_id)``，事件流自动 replay
        - ``thread_id`` 不存在 → ``create(persona=default, model=default, session_id=thread_id)``
          落盘到 ``agent.paths.sessions_dir()``，首行 ``session_meta``

        本期不暴露"客户端能控制 auto-create 时用哪个 persona / model"——
        AG-UI 协议本身没有这个槽位。需要切 persona / model 时，客户端走
        ``POST /v1/sessions/{id}/persona|model``（design §4.11）。

        Args:
            boot: 解析完成的 AG-UI 请求中可用的全部信息。

        Returns:
            装配好的 :class:`Conversation`，可立刻调用 ``stream(new_user_input)``。

        Raises:
            agent.PersonaNotFoundError: ``default_persona`` 在 catalog 找不到。
            agent.SessionCorruptError: 已存在的 jsonl 文件解析失败。
            llm_providers.LLMAuthError: 工厂构造 :class:`LLMClient` 失败。
        """
        mgr = self._runtime.persistent_session_manager

        try:
            session = mgr.open(boot.thread_id)
        except SessionNotFoundError:
            persona_info = self._runtime.catalog.find_by_name(boot.default_persona)
            session = mgr.create(
                persona=persona_info.name,
                model=boot.default_model,
                persona_id=persona_info.id,
                session_id=boot.thread_id,
            )

        return mgr.start_conversation(session)

    @staticmethod
    def _append_history_message_in_memory(session: Session, msg: Message) -> None:
        """把一条历史 :class:`Message` 转成对应的 :class:`Event` 直接 append 到
        :class:`Session` 内存事件流；**不触发** ``store.append_event``。

        这是 transient 分支的关键 trick：我们没有真实的"会话创建 → 多轮 append"
        过程，只是把客户端发来的历史"伪装"成事件流，让 :class:`Conversation`
        在下一轮 ``stream`` 时把这些消息作为上下文喂给 LLM。``NullSessionStore``
        保证即使后续 ``Conversation`` 自己 ``append_event``（落 user / assistant
        消息）也不会动磁盘。
        """
        from datetime import UTC, datetime
        from uuid import uuid4

        from agent import Event

        ts = msg.timestamp if msg.timestamp.tzinfo else datetime.now(UTC)

        if msg.role == "user":
            session.append(
                Event(
                    type="user_message",
                    uuid=msg.uuid or str(uuid4()),
                    ts=ts,
                    payload={"content": msg.content},
                )
            )
        elif msg.role == "assistant":
            session.append(
                Event(
                    type="assistant_message",
                    uuid=msg.uuid or str(uuid4()),
                    ts=ts,
                    payload={"content": msg.content, "partial": False},
                    meta={"persona": session.current_persona, "model": session.current_model},
                )
            )
