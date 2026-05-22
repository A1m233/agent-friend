"""``SessionBridge`` —— bridge 进程的"会话桥"。

承担三种语义模式的统一调度（本期 M6.1 只实现其中两种与 OpenAI 相关的部分）：

1. **transient（OpenAI 默认无状态）**：客户端在 messages 数组里发完整历史；
   bridge 进程内为这一次请求构造 in-memory :class:`Session` + :class:`Conversation`，
   跑完即丢——磁盘不留任何痕迹。
2. **persistent（OpenAI 扩展位 / AG-UI）**：客户端通过 ``X-Agent-Friend-Session-Id``
   header（OpenAI）或 ``thread_id`` 字段（AG-UI）指定持久化 session id；
   bridge 读 / 写 ``data/sessions/{id}.jsonl``。
3. **auto-create（AG-UI）**：客户端发的 ``thread_id`` 不存在时，自动以该
   ``thread_id`` 作为 ``session_id`` 创建新 session（详见 design §4.4 N-2）。

M6.1 范围：只实现 **transient** 模式。**persistent / auto-create** 留 M6.2
接入 AG-UI 时再加。本类暴露的接口已为后续模式预留扩展位（同样返回
:class:`agent.Conversation` 与一次性 in-memory :class:`Session`）。

详见 docs/requirements/006-agent-bridge/design.md §4.4。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent import Conversation, Message, Session

from .assembly import BridgeRuntime


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
