"""IMRouter(022 起):IM inbound → agent 主链路 → IM outbound 的转发层。

核心职责:

1. **决定挂哪个 session**(策略点 · 扩展点 · :meth:`session_id_for`):
   本期实装 = ``f"im:{im_type}:{event.chat_id}"`` —— 每个 IM user 一个独立的、
   永久复用的 session。**未来若切到"IM 跟桌宠共享 session" / "按时间切" /
   "按主题切"等其他路线**,重写这个 method 即可(subclass override),业务零改动。

2. **跑一轮 Conversation**(:meth:`_run_turn_sync`):
   走现成的 :meth:`SessionBridge.bind_persistent` 拿 :class:`Conversation`,
   消费 ``conv.stream`` 同步 generator,聚合 :class:`TextDelta` 文本到 buffer,
   等 stream 自然结束。``ToolCallRequest`` / ``ToolCallResult`` 在 agent 主链路
   自处理,IM 通道**不感知**(本期范围)。

3. **同步 generator 跑在线程池**(:meth:`handle_inbound`):
   ``Conversation.stream`` 是同步 generator(详见 ``agent.Conversation`` 设计),
   而 QQ adapter 跑在 asyncio loop 上 —— 用 ``asyncio.to_thread`` 把同步 stream
   跑到线程池,把整轮聚合后的文本回写到 outbound。

4. **错误兜底**(channel 层 fallback):
   ``Conversation.stream`` 跑挂时回固定文案 :data:`FALLBACK_TEXT` —— 不走 persona
   渲染(语义不该过 persona 层),不影响 IM 长连本身。43xxx/11xxx 等 QQ 平台
   错误码在 adapter 层兜底,Router 不感知。

5. **跨通道"同一个它"是 free 的**(不需要 Router 显式干预):
   IM session 跟桌宠 session 共享 ``owner_user_id=DEFAULT_OWNER_USER_ID``,
   每轮结束 ``agent.runtime`` 的 PostTurn hook 自动 ``memory.observe`` ——
   记忆跨 session 召回,无需 Router 介入。

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.2。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from agent import TextDelta

from ...session_bridge import PersistentBootstrap, SessionBridge
from .content import OutboundContent
from .event import InboundEvent

__all__ = ["FALLBACK_TEXT", "IMRouter"]

logger = logging.getLogger(__name__)


FALLBACK_TEXT = "我现在有点问题,稍后再试 🤖"
"""``Conversation.stream`` 抛错时 IM 回写的兜底文案。

固定文案,不走 persona 渲染 —— 这是 channel 层 fallback,语义不该过 persona 层
(persona 是 agent 主链路的概念,channel 出问题时连主链路都跑不起来)。
"""


SendCallback = Callable[[OutboundContent], Awaitable[None]]
"""IM adapter 提供给 Router 的回写回调签名。"""


class IMRouter:
    """IM inbound → agent 主链路 → IM outbound 的转发层。

    Args:
        session_bridge: 复用现成的 :class:`SessionBridge`,走它的 persistent
            模式,session_id 由 :meth:`session_id_for` 决定。
        default_persona: ``PersistentBootstrap.default_persona`` 用的默认人格 slug
            (从 :class:`BridgeRuntime` 拿)。
        default_model: ``PersistentBootstrap.default_model`` 用的默认 LLM model
            (从 :class:`BridgeRuntime` 拿)。
    """

    def __init__(
        self,
        session_bridge: SessionBridge,
        default_persona: str,
        default_model: str,
    ) -> None:
        self._bridge = session_bridge
        self._default_persona = default_persona
        self._default_model = default_model

    def session_id_for(self, im_type: str, event: InboundEvent) -> str:
        """计算 IM inbound 应该挂在哪个 session 上。**策略扩展点**。

        本期实装 = ``f"im:{im_type}:{event.chat_id}"``:每个 IM user(``chat_id``)
        一个独立的、永久复用的 session。session_id 稳定 → 现有
        :class:`JsonlSessionStore` 自动落盘 → 重启 / 重连不丢上下文。

        **未来路线**(改这个 method 即可,业务代码零改动):

        - 跟桌宠共享 session:``return current_desktop_session_id()``
        - 按时间切:``return f"im:{im_type}:{event.chat_id}:{date()}"``
        - 按主题切:``return classify_topic(event.content)``

        Args:
            im_type: 平台标识(``"qq"`` 等)。
            event: inbound 事件,本期实装只用 ``event.chat_id``;子类策略可以
                读 ``event.content`` / ``event.timestamp`` 等做更复杂决策。

        Returns:
            session_id 字符串,作为 :class:`PersistentBootstrap.thread_id` 直接走
            :meth:`SessionBridge.bind_persistent`。
        """
        return f"im:{im_type}:{event.chat_id}"

    async def handle_inbound(
        self,
        im_type: str,
        event: InboundEvent,
        send_fn: SendCallback,
    ) -> None:
        """处理一条 IM inbound:装配 Conversation → 跑一轮 → 回写 outbound。

        Args:
            im_type: 平台标识,用于 :meth:`session_id_for`。
            event: inbound 事件;``event.content`` 作为 ``new_user_input`` 喂给
                ``Conversation.stream``。
            send_fn: adapter 提供的回写回调,接 :class:`OutboundContent`。

        异常策略:
            ``Conversation.stream`` / ``bind_persistent`` 抛任何异常 → log
            exception + 回写 :data:`FALLBACK_TEXT`,**不向上抛**(IM 长连不该
            因为单轮挂掉而断开)。
        """
        session_id = self.session_id_for(im_type, event)
        boot = PersistentBootstrap(
            thread_id=session_id,
            new_user_input=event.content,
            default_persona=self._default_persona,
            default_model=self._default_model,
        )

        try:
            text = await asyncio.to_thread(self._run_turn_sync, boot)
        except Exception:
            logger.exception("IM turn 跑挂 (im_type=%s, session=%s)", im_type, session_id)
            text = FALLBACK_TEXT

        # 跑通了但 agent 一字未输出(罕见):也回 fallback,避免发空消息给 IM
        if not text:
            text = FALLBACK_TEXT

        await send_fn(
            OutboundContent(
                chat_id=event.chat_id,
                chat_scope=event.chat_scope,
                text=text,
                reply_to_message_id=event.message_id,
            )
        )

    def _run_turn_sync(self, boot: PersistentBootstrap) -> str:
        """跑一轮 :class:`Conversation`,聚合所有 :class:`TextDelta` 到完整文本。

        ``Conversation.stream`` 是同步 generator,会 emit ``TextDelta`` /
        ``ToolCallRequest`` / ``ToolCallResult`` / ``TurnDone``。IM 通道**不
        流式分片**(QQ c2c 无打字态,分片只是切碎),所以聚合后整段回写。

        ``ToolCallRequest`` / ``ToolCallResult``:agent 主链路自处理(执行工具
        + 把结果喂回 LLM),IM 通道不感知 —— 我们只关心最终 assistant 文本。

        Args:
            boot: 已构造好的 :class:`PersistentBootstrap`。

        Returns:
            聚合后的完整文本(可能空字符串,如果 LLM 没输出任何 text)。
        """
        conv = self._bridge.bind_persistent(boot)
        buf: list[str] = []
        for ev in conv.stream(boot.new_user_input):
            if isinstance(ev, TextDelta):
                buf.append(ev.text)
            # TurnDone:不 break,让 generator 自然 return — 跟 ag_ui/openai encoder 同款约定。
            # 早 break 会触发 generator close 抛 GeneratorExit,而 Conversation.stream 的
            # finally 把它误判为业务中断,落一份 partial=True 的 assistant_event;下一轮组装
            # prompt 时 history 里同条 assistant 文本出现两次 → LLM 自洽地真把回复说两遍。
            # ToolCallRequest / ToolCallResult:agent 主链路自处理,IM 不感知
        return "".join(buf)
