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
from uuid import uuid4

from agent.attachments import AttachmentRef
from agent.runtime import AgentRuntime, RunOutcome, RunRequest, UserEvent
from agent.system_prompt import IM_QQ_SURFACE, InteractionSurfaceId

from agent import Conversation, TextDelta
from llm_providers import model_capabilities

from ...errors import map_exception
from ...session_bridge import PersistentBootstrap, SessionBridge
from .content import OutboundContent, OutboundTextFormat
from .control import menu_controls
from .event import InboundEvent
from .provider import IMOutboundProvider
from .streaming import (
    IMStreamSession,
    IMTextStreamingProvider,
    OutboundStreamContext,
    StreamRelay,
)

__all__ = ["FALLBACK_TEXT", "IMRouter"]

logger = logging.getLogger(__name__)


FALLBACK_TEXT = "我现在有点问题,稍后再试 🤖"
"""``Conversation.stream`` 抛错时 IM 回写的兜底文案。

固定文案,不走 persona 渲染 —— 这是 channel 层 fallback,语义不该过 persona 层
(persona 是 agent 主链路的概念,channel 出问题时连主链路都跑不起来)。
"""


SendCallback = Callable[[OutboundContent], Awaitable[None]]
"""IM adapter 提供给 Router 的回写回调签名。"""

_STREAM_END = object()
_STREAM_INTERRUPTED_SUFFIX = "\n\n（回复生成中断，请稍后重试。）"
_STREAM_MENU_TEXT = "操作"
_STREAM_MENU_DELAY_SECONDS = 0.8
_IM_SURFACES: dict[str, InteractionSurfaceId] = {"qq": IM_QQ_SURFACE}


def _surface_for_im(im_type: str) -> InteractionSurfaceId:
    try:
        return _IM_SURFACES[im_type]
    except KeyError as exc:
        raise RuntimeError(f"IM adapter missing interaction surface: {im_type}") from exc


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
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self._bridge = session_bridge
        self._default_persona = default_persona
        self._default_model = default_model
        self._agent_runtime = agent_runtime

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
        provider_or_send: IMOutboundProvider | SendCallback,
        attachments: tuple[AttachmentRef, ...] = (),
        *,
        session_id: str | None = None,
    ) -> None:
        """处理一条 IM inbound:装配 Conversation → 跑一轮 → 回写 outbound。

        Args:
            im_type: 平台标识,用于 :meth:`session_id_for`。
            event: inbound 事件;``event.content`` 作为 ``new_user_input`` 喂给
                ``Conversation.stream``。
            provider_or_send: adapter 实例；兼容旧调用方直接传 send callback。

        异常策略:
            ``Conversation.stream`` / ``bind_persistent`` 抛任何异常 → log
            exception + 回写 :data:`FALLBACK_TEXT`,**不向上抛**(IM 长连不该
            因为单轮挂掉而断开)。
        """
        send_fn = _send_callback(provider_or_send)
        session_id = session_id or self.session_id_for(im_type, event)
        logger.info(
            "IM inbound router start im_type=%s session_id=%s chat_scope=%s chat_id=%s "
            "message_id=%s content_len=%d stripped_content_len=%d attachment_count=%d",
            im_type,
            session_id,
            event.chat_scope,
            event.chat_id,
            event.message_id,
            _text_len(event.content),
            _text_len((event.content or "").strip()),
            _attachment_count(event),
        )
        boot = PersistentBootstrap(
            thread_id=session_id,
            new_user_input=event.content,
            default_persona=self._default_persona,
            default_model=self._default_model,
        )

        if attachments:
            model = self._bridge.persistent_model(session_id, default=self._default_model)
            capabilities = model_capabilities(model)
            unsupported = (
                not capabilities.attachments
                or len(attachments) > capabilities.max_attachments_per_turn
                or sum(item.size_bytes for item in attachments)
                > capabilities.max_total_attachment_bytes
                or any(
                    item.media_type not in capabilities.accepted_media_types for item in attachments
                )
            )
            if unsupported:
                await send_fn(
                    OutboundContent(
                        chat_id=event.chat_id,
                        chat_scope=event.chat_scope,
                        text="当前会话使用的模型不支持这类附件，请先切换到支持图片或文档的模型。",
                        reply_to_message_id=event.message_id,
                    )
                )
                return

        text_format: OutboundTextFormat = "markdown"
        include_menu = True
        stream_trace = uuid4().hex[:12]
        run_id = uuid4().hex if self._agent_runtime is not None else None
        stream_session = self._open_stream(
            provider_or_send,
            event,
            attachments=attachments,
            stream_trace=stream_trace,
            run_id=run_id,
        )
        relay: StreamRelay | None = None
        delta_queue: asyncio.Queue[object] | None = None
        relay_task: asyncio.Task[None] | None = None
        on_text_delta: Callable[[str], None] | None = None
        if stream_session is not None:
            delta_queue = asyncio.Queue()
            relay = StreamRelay(stream_session, stream_trace=stream_trace)
            relay_task = asyncio.create_task(relay.feed_from_queue(delta_queue, _STREAM_END))
            loop = asyncio.get_running_loop()

            def publish_delta(delta: str) -> None:
                loop.call_soon_threadsafe(delta_queue.put_nowait, delta)

            on_text_delta = publish_delta
            logger.info(
                "IM stream eligible stream_trace=%s run_id=%s im_type=%s session_id=%s "
                "message_id=%s chat_scope=%s",
                stream_trace,
                run_id or "",
                im_type,
                session_id,
                event.message_id,
                event.chat_scope,
            )
        try:
            surface = _surface_for_im(im_type)
            text = await asyncio.to_thread(
                self._run_turn_sync,
                boot,
                attachments,
                on_text_delta,
                run_id,
                surface,
            )
        except Exception as exc:
            partial_text = await _finish_relay(delta_queue, relay_task, relay)
            logger.exception(
                "IM turn 跑挂 (im_type=%s, session=%s, message_id=%s, content_len=%d, "
                "stream_trace=%s, stream_started=%s)",
                im_type,
                session_id,
                event.message_id,
                _text_len(event.content),
                stream_trace,
                stream_session.has_started if stream_session is not None else False,
            )
            if stream_session is not None and stream_session.has_started:
                interrupted = (partial_text or FALLBACK_TEXT) + _STREAM_INTERRUPTED_SUFFIX
                await stream_session.abort(interrupted, type(exc).__name__)
                return
            text = FALLBACK_TEXT
            text_format = "plain_text"
            include_menu = False
        else:
            await _finish_relay(delta_queue, relay_task, relay)

        # 跑通了但 agent 一字未输出(罕见):也回 fallback,避免发空消息给 IM
        if not text:
            logger.warning(
                "IM inbound router empty agent output im_type=%s session_id=%s message_id=%s",
                im_type,
                session_id,
                event.message_id,
            )
            text = FALLBACK_TEXT
            text_format = "plain_text"
            include_menu = False

        logger.info(
            "IM inbound router reply im_type=%s session_id=%s message_id=%s "
            "reply_len=%d text_format=%s",
            im_type,
            session_id,
            event.message_id,
            _text_len(text),
            text_format,
        )

        if stream_session is not None and stream_session.has_started:
            await stream_session.finish(text)
            logger.info(
                "IM stream final result stream_trace=%s run_id=%s message_id=%s "
                "content_len=%d completed=%s had_failure=%s duplicate_fallback=false",
                stream_trace,
                run_id or "",
                event.message_id,
                _text_len(text),
                stream_session.is_completed,
                stream_session.is_failed,
            )
            if stream_session.is_completed and include_menu:
                logger.info(
                    "IM stream menu scheduled stream_trace=%s run_id=%s message_id=%s delay_ms=%d",
                    stream_trace,
                    run_id or "",
                    event.message_id,
                    round(_STREAM_MENU_DELAY_SECONDS * 1000),
                )
                await asyncio.sleep(_STREAM_MENU_DELAY_SECONDS)
                await send_fn(
                    OutboundContent(
                        chat_id=event.chat_id,
                        chat_scope=event.chat_scope,
                        text=_STREAM_MENU_TEXT,
                        text_format="markdown",
                        reply_to_message_id=event.message_id,
                        controls=menu_controls(),
                    )
                )
            return

        if stream_session is not None and stream_session.is_failed:
            logger.warning(
                "IM stream falling back to static stream_trace=%s run_id=%s message_id=%s "
                "content_len=%d",
                stream_trace,
                run_id or "",
                event.message_id,
                _text_len(text),
            )

        await send_fn(
            OutboundContent(
                chat_id=event.chat_id,
                chat_scope=event.chat_scope,
                text=text,
                text_format=text_format,
                reply_to_message_id=event.message_id,
                controls=menu_controls() if include_menu else None,
            )
        )

    def _run_turn_sync(
        self,
        boot: PersistentBootstrap,
        attachments: tuple[AttachmentRef, ...] = (),
        on_text_delta: Callable[[str], None] | None = None,
        run_id: str | None = None,
        surface: InteractionSurfaceId | None = None,
    ) -> str:
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
        buf: list[str] = []
        if self._agent_runtime is not None:
            handle = self._agent_runtime.submit(
                UserEvent(session_id=boot.thread_id, user_input=boot.new_user_input),
                run_id=run_id,
                channel="text",
                surface=surface,
                conversation_factory=lambda: self._bridge.bind_persistent(boot),
                conversation_runner=lambda conv, request, token: conv.stream(
                    boot.new_user_input,
                    attachments=list(attachments),
                    channel=request.channel,
                    surface=request.surface,
                    run_id=request.run_id,
                    cancel_check=token.raise_if_cancelled,
                ),
                error_callback=self._persist_run_error,
                metadata={"protocol": "im"},
            )
            for ev in handle.events():
                if isinstance(ev, TextDelta):
                    buf.append(ev.text)
                    if on_text_delta is not None:
                        on_text_delta(ev.text)
            terminal = handle.terminal
            if terminal is None or terminal.outcome is not RunOutcome.COMPLETED:
                if terminal is not None and terminal.error is not None:
                    raise terminal.error
                raise RuntimeError(
                    terminal.reason_code if terminal is not None else "runtime terminated"
                )
            return "".join(buf)

        with self._bridge.session_turn(boot.thread_id):
            conv = self._bridge.bind_persistent(boot)
            try:
                for ev in conv.stream(
                    boot.new_user_input,
                    attachments=list(attachments),
                    channel="text",
                    surface=surface,
                ):
                    if isinstance(ev, TextDelta):
                        buf.append(ev.text)
                        if on_text_delta is not None:
                            on_text_delta(ev.text)
                    # TurnDone:不 break,让 generator 自然 return — 跟 ag_ui/openai encoder 同款约定。
                    # 早 break 会触发 generator close 抛 GeneratorExit,而 Conversation.stream 的
                    # finally 把它误判为业务中断,落一份 partial=True 的 assistant_event;下一轮组装
                    # prompt 时 history 里同条 assistant 文本出现两次 → LLM 自洽地真把回复说两遍。
                    # ToolCallRequest / ToolCallResult:agent 主链路自处理,IM 不感知
            except BaseException as exc:
                self._persist_run_error(conv, exc, None)
                raise
        return "".join(buf)

    @staticmethod
    def _open_stream(
        provider_or_send: IMOutboundProvider | SendCallback,
        event: InboundEvent,
        *,
        attachments: tuple[AttachmentRef, ...],
        stream_trace: str,
        run_id: str | None,
    ) -> IMStreamSession | None:
        if attachments:
            return None
        if not isinstance(provider_or_send, IMTextStreamingProvider):
            return None
        return provider_or_send.open_text_stream(
            OutboundStreamContext(
                chat_id=event.chat_id,
                chat_scope=event.chat_scope,
                reply_to_message_id=event.message_id,
                text_format="markdown",
                stream_trace=stream_trace,
                run_id=run_id,
            )
        )

    @staticmethod
    def _persist_run_error(
        conv: Conversation,
        exc: BaseException,
        request: RunRequest | None,
    ) -> None:
        """把 IM 终态失败写入共享 session，让桌面端刷新后看到同一结果。"""
        err = map_exception(exc)
        run_id = getattr(request, "run_id", None)
        try:
            conv.append_run_error_event(
                code=err.code,
                message=err.message,
                recoverable=err.recoverable,
                source="im",
                run_id=run_id if isinstance(run_id, str) else None,
                sanitize_failed_model_call=err.sanitize_failed_model_call,
            )
        except Exception:
            logger.warning("IM run_error 持久化失败", exc_info=True)


def _text_len(value: object) -> int:
    return len(value) if isinstance(value, str) else 0


def _attachment_count(event: InboundEvent) -> int:
    attachments = getattr(event, "attachments", None)
    return len(attachments) if isinstance(attachments, list) else 0


def _send_callback(provider_or_send: IMOutboundProvider | SendCallback) -> SendCallback:
    if isinstance(provider_or_send, IMOutboundProvider):
        return provider_or_send.send
    if callable(provider_or_send):
        return provider_or_send
    raise TypeError("IM provider must expose send()")


async def _finish_relay(
    queue: asyncio.Queue[object] | None,
    task: asyncio.Task[None] | None,
    relay: StreamRelay | None,
) -> str:
    if queue is None or task is None or relay is None:
        return ""
    # Worker callbacks are scheduled on this loop before the to_thread future
    # completes. Yield once so they enter the queue before the terminal marker.
    await asyncio.sleep(0)
    await queue.put(_STREAM_END)
    await task
    return relay.full_text
