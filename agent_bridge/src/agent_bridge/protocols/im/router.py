"""IMRouter(022 起):IM inbound → agent 主链路 → IM outbound 的转发层。

核心职责:

1. **决定挂哪个 session**(策略点 · 扩展点 · :meth:`session_id_for`):
   本期实装 = ``f"im:{im_type}:{event.chat_id}"`` —— 每个 IM user 一个独立的、
   永久复用的 session。**未来若切到"IM 跟桌宠共享 session" / "按时间切" /
   "按主题切"等其他路线**,重写这个 method 即可(subclass override),业务零改动。

2. **跑一轮 Conversation**(:meth:`_run_turn_sync`):
   走现成的 :meth:`SessionBridge.bind_persistent` 拿 :class:`Conversation`,
   消费 ``conv.stream`` 同步 generator,聚合 :class:`TextDelta` 文本到 buffer,
   等 stream 自然结束。工具请求与结果正文不会发到 IM，但会保留
   工具调用前后的 assistant 段落边界。

3. **同步 generator 跑在线程池**(:meth:`handle_inbound`):
   ``Conversation.stream`` 是同步 generator(详见 ``agent.Conversation`` 设计),
   而 QQ adapter 跑在 asyncio loop 上 —— 用 ``asyncio.to_thread`` 把同步 stream
   跑到线程池,把整轮聚合后的文本回写到 outbound。

4. **错误兜底**(channel 层 fallback):
   ``Conversation.stream`` 跑挂时通过共享错误映射返回安全、可理解的原因文案；
   未识别错误和空输出才使用 :data:`FALLBACK_TEXT`。文案不走 persona 渲染
   (语义不该过 persona 层),也不影响 IM 长连本身。43xxx/11xxx 等 QQ 平台错误码
   在 adapter 层兜底,Router 不感知。

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

from agent.attachments import AttachmentError, AttachmentRef
from agent.attachments.input_policy import AttachmentInputPolicy
from agent.latency import RunLatencyContext, log_run_latency, monotonic_ms
from agent.message_references import QuotedTextReference
from agent.runtime import AgentRuntime, RunOutcome, RunRequest, UserEvent
from agent.system_prompt import IM_QQ_SURFACE, InteractionSurfaceId
from llm_providers.catalog import ModelRouteCapabilities

from agent import (
    AssistantMessageMetadata,
    ContextCompactionStatus,
    Conversation,
    TextDelta,
    ToolCallResult,
)
from llm_providers import model_capabilities

from ...errors import map_exception
from ...session_bridge import PersistentBootstrap, SessionBridge
from .content import OutboundContent, OutboundTextFormat
from .control import menu_controls
from .event import InboundEvent
from .media import InboundMedia, render_model_media_context, render_user_media_notice
from .provider import IMOutboundProvider, OutboundSendResult
from .qq_references import qq_current_message_aliases
from .reference_aliases import IMReferenceAliasIndex
from .streaming import (
    IMStreamSession,
    IMTextStreamingProvider,
    OutboundStreamContext,
    StreamProgressUpdate,
    StreamRelay,
)

__all__ = ["FALLBACK_TEXT", "IMRouter"]

logger = logging.getLogger(__name__)


FALLBACK_TEXT = "这轮回答中断了，没有生成完整回复。请稍后重试。"
"""未识别异常或空输出时 IM 回写的兜底文案。

固定文案,不走 persona 渲染 —— 这是 channel 层 fallback,语义不该过 persona 层
(persona 是 agent 主链路的概念,channel 出问题时连主链路都跑不起来)。
"""


SendCallback = Callable[[OutboundContent], Awaitable[OutboundSendResult | None]]
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


def _missing_tool_boundary(parts: list[str], incoming: str) -> str:
    """让工具调用前后的 assistant 文本至少相隔一个 Markdown 段落。"""
    existing = "".join(parts)
    trailing_newlines = len(existing) - len(existing.rstrip("\n"))
    leading_newlines = len(incoming) - len(incoming.lstrip("\n"))
    return "\n" * max(0, 2 - trailing_newlines - leading_newlines)


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
        reference_aliases: IMReferenceAliasIndex | None = None,
        capability_resolver: Callable[[str], ModelRouteCapabilities] = model_capabilities,
        session_files_available: Callable[[], bool] | None = None,
    ) -> None:
        self._capability_resolver = capability_resolver
        self._session_files_available = session_files_available or (lambda: False)
        self._bridge = session_bridge
        self._default_persona = default_persona
        self._default_model = default_model
        self._agent_runtime = agent_runtime
        self._reference_aliases = reference_aliases

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
        media: tuple[InboundMedia, ...] = (),
        references: tuple[QuotedTextReference, ...] = (),
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
            exception + 回写共享错误映射产生的安全文案,**不向上抛**(IM 长连
            不该因为单轮挂掉而断开)。
        """
        latency_started_ms = monotonic_ms()
        send_fn = _send_callback(provider_or_send)
        session_id = session_id or self.session_id_for(im_type, event)
        user_event_id = uuid4().hex
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
            capabilities = self._capability_resolver(model)
            try:
                AttachmentInputPolicy(capabilities, self._session_files_available()).validate(
                    list(attachments)
                )
            except AttachmentError as exc:
                await send_fn(
                    OutboundContent(
                        chat_id=event.chat_id,
                        chat_scope=event.chat_scope,
                        text=str(exc),
                        reply_to_message_id=event.message_id,
                    )
                )
                return

        model_media_context = render_model_media_context(media)
        media_notice = render_user_media_notice(media)
        reply_prefix = f"（{media_notice}）\n\n" if media_notice else ""

        text_format: OutboundTextFormat = "markdown"
        include_menu = True
        stream_trace = uuid4().hex[:12]
        run_id = uuid4().hex if self._agent_runtime is not None else None
        run_latency = (
            RunLatencyContext(
                run_id=run_id,
                session_id=session_id,
                started_ms=latency_started_ms,
            )
            if run_id is not None
            else None
        )
        log_run_latency(
            logger,
            run_latency,
            "im_turn_start",
            im_type=im_type,
            stream_trace=stream_trace,
        )
        stream_session = self._open_stream(
            provider_or_send,
            event,
            stream_trace=stream_trace,
            run_id=run_id,
            session_id=session_id,
            latency_started_ms=latency_started_ms,
        )
        relay: StreamRelay | None = None
        delta_queue: asyncio.Queue[object] | None = None
        relay_task: asyncio.Task[None] | None = None
        on_text_delta: Callable[[str], None] | None = None
        on_compaction_progress: Callable[[StreamProgressUpdate], None] | None = None
        if stream_session is not None:
            delta_queue = asyncio.Queue()
            relay = StreamRelay(stream_session, stream_trace=stream_trace)
            relay_task = asyncio.create_task(relay.feed_from_queue(delta_queue, _STREAM_END))
            loop = asyncio.get_running_loop()

            def publish_delta(delta: str) -> None:
                loop.call_soon_threadsafe(delta_queue.put_nowait, delta)

            on_text_delta = publish_delta

            def publish_progress(update: StreamProgressUpdate) -> None:
                loop.call_soon_threadsafe(delta_queue.put_nowait, update)

            on_compaction_progress = publish_progress
            if reply_prefix:
                delta_queue.put_nowait(reply_prefix)
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
            origin_destination_id = _origin_destination_id(
                provider_or_send,
                im_type=im_type,
                event=event,
                session_id=session_id,
            )
            agent_text = await asyncio.to_thread(
                self._run_turn_sync,
                boot,
                attachments,
                references,
                on_text_delta,
                on_compaction_progress,
                run_id,
                model_media_context,
                surface,
                latency_started_ms,
                origin_destination_id,
                user_event_id,
            )
        except Exception as exc:
            fallback_text = map_exception(exc).message
            log_run_latency(
                logger,
                run_latency,
                "im_agent_failed",
                error_type=type(exc).__name__,
            )
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
                self._register_user_alias(
                    provider_or_send,
                    im_type=im_type,
                    event=event,
                    session_id=session_id,
                    user_event_id=user_event_id,
                )
                interrupted = (partial_text or fallback_text) + _STREAM_INTERRUPTED_SUFFIX
                await stream_session.abort(interrupted, type(exc).__name__)
                log_run_latency(
                    logger,
                    run_latency,
                    "im_turn_done",
                    outcome="failed",
                    delivery="stream_aborted",
                )
                return
            agent_text = fallback_text
            text = reply_prefix + fallback_text
            text_format = "plain_text"
            include_menu = False
        else:
            log_run_latency(
                logger,
                run_latency,
                "im_agent_done",
                response_chars=len(agent_text),
            )
            await _finish_relay(delta_queue, relay_task, relay)
            text = reply_prefix + agent_text

        self._register_user_alias(
            provider_or_send,
            im_type=im_type,
            event=event,
            session_id=session_id,
            user_event_id=user_event_id,
        )
        assistant_event = self._bridge.assistant_event_after(session_id, user_event_id)
        assistant_sources = tuple(
            item.uuid for item in self._bridge.assistant_events_after(session_id, user_event_id)
        )

        # 跑通了但 agent 一字未输出(罕见):也回 fallback,避免发空消息给 IM
        if not agent_text:
            logger.warning(
                "IM inbound router empty agent output im_type=%s session_id=%s message_id=%s",
                im_type,
                session_id,
                event.message_id,
            )
            text = reply_prefix + FALLBACK_TEXT
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
            if stream_session.is_completed and assistant_event is not None:
                self._register_outbound_alias(
                    provider_or_send,
                    im_type=im_type,
                    event=event,
                    session_id=session_id,
                    event_uuid=assistant_event.uuid,
                    event_uuids=assistant_sources,
                    provider_message_id=getattr(stream_session, "provider_message_id", None),
                )
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
            log_run_latency(
                logger,
                run_latency,
                "im_turn_done",
                outcome="completed" if stream_session.is_completed else "delivery_failed",
                delivery="native_stream",
                response_chars=_text_len(text),
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

        send_result = await send_fn(
            OutboundContent(
                chat_id=event.chat_id,
                chat_scope=event.chat_scope,
                text=text,
                text_format=text_format,
                reply_to_message_id=event.message_id,
                controls=menu_controls() if include_menu else None,
            )
        )
        if assistant_event is not None and send_result is not None:
            self._register_outbound_alias(
                provider_or_send,
                im_type=im_type,
                event=event,
                session_id=session_id,
                event_uuid=assistant_event.uuid,
                event_uuids=assistant_sources,
                provider_message_id=send_result.provider_message_id,
            )
        log_run_latency(
            logger,
            run_latency,
            "im_turn_done",
            outcome="completed",
            delivery="static",
            response_chars=_text_len(text),
        )

    def _run_turn_sync(
        self,
        boot: PersistentBootstrap,
        attachments: tuple[AttachmentRef, ...] = (),
        references: tuple[QuotedTextReference, ...] = (),
        on_text_delta: Callable[[str], None] | None = None,
        on_compaction_progress: Callable[[StreamProgressUpdate], None] | None = None,
        run_id: str | None = None,
        turn_context: str | None = None,
        surface: InteractionSurfaceId | None = None,
        latency_started_ms: int | None = None,
        interaction_destination_id: str | None = None,
        user_event_id: str | None = None,
    ) -> str:
        """跑一轮 :class:`Conversation`,聚合所有 :class:`TextDelta` 到完整文本。

        ``Conversation.stream`` 是同步 generator,会 emit ``TextDelta`` /
        ``ToolCallRequest`` / ``ToolCallResult`` / ``TurnDone``。IM 通道**不
        流式分片**(QQ c2c 无打字态,分片只是切碎),所以聚合后整段回写。

        ``ToolCallRequest`` / ``ToolCallResult``:agent 主链路自处理(执行工具
        + 把结果喂回 LLM)。IM 不透出工具载荷，但在工具结果后的
        下一段 assistant 文本前保留段落边界。

        Args:
            boot: 已构造好的 :class:`PersistentBootstrap`。

        Returns:
            聚合后的完整文本(可能空字符串,如果 LLM 没输出任何 text)。
        """
        buf: list[str] = []
        tool_boundary_pending = False

        def consume_event(ev: object) -> None:
            nonlocal tool_boundary_pending

            if isinstance(ev, ContextCompactionStatus):
                if on_compaction_progress is not None:
                    on_compaction_progress(StreamProgressUpdate(phase=ev.phase))
                return
            if isinstance(ev, ToolCallResult):
                tool_boundary_pending = bool(buf)
                return
            if isinstance(ev, AssistantMessageMetadata):
                sources: list[str] = []
                for part in ev.content_parts:
                    for source in part.get("annotations", []):
                        url = source.get("url", "")
                        label = source.get("title") or source.get("filename") or "外部文件引用"
                        sources.append(
                            f"{label}: {url}"
                            if isinstance(url, str) and url.startswith(("https://", "http://"))
                            else f"{label}（当前无法打开）"
                        )
                if sources:
                    suffix = "\n\n来源：\n" + "\n".join(dict.fromkeys(sources))
                    buf.append(suffix)
                    if on_text_delta is not None:
                        on_text_delta(suffix)
                return
            if not isinstance(ev, TextDelta) or not ev.text:
                return

            if tool_boundary_pending:
                separator = _missing_tool_boundary(buf, ev.text)
                if separator:
                    buf.append(separator)
                    if on_text_delta is not None:
                        on_text_delta(separator)
                tool_boundary_pending = False

            buf.append(ev.text)
            if on_text_delta is not None:
                on_text_delta(ev.text)

        if self._agent_runtime is not None:
            handle = self._agent_runtime.submit(
                UserEvent(session_id=boot.thread_id, user_input=boot.new_user_input),
                run_id=run_id,
                latency_started_ms=latency_started_ms,
                channel="text",
                surface=surface,
                conversation_factory=lambda: self._bridge.bind_persistent(
                    boot,
                    post_turn_external=True,
                ),
                conversation_runner=lambda conv, request, token: conv.stream(
                    boot.new_user_input,
                    attachments=list(attachments),
                    references=list(references),
                    user_event_id=user_event_id,
                    channel=request.channel,
                    surface=request.surface,
                    interaction_destination_id=(
                        str(request.metadata["interaction_destination_id"])
                        if request.metadata.get("interaction_destination_id")
                        else None
                    ),
                    run_id=request.run_id,
                    cancel_check=token.raise_if_cancelled,
                    turn_context=turn_context,
                ),
                error_callback=self._persist_run_error,
                metadata={
                    "protocol": "im",
                    "interaction_destination_id": interaction_destination_id,
                },
            )
            for ev in handle.events():
                consume_event(ev)
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
                    references=list(references),
                    user_event_id=user_event_id,
                    channel="text",
                    turn_context=turn_context,
                    surface=surface,
                    interaction_destination_id=interaction_destination_id,
                ):
                    consume_event(ev)
                    # TurnDone:不 break,让 generator 自然 return — 跟 ag_ui/openai encoder 同款约定。
                    # 早 break 会触发 generator close 抛 GeneratorExit,而 Conversation.stream 的
                    # finally 把它误判为业务中断,落一份 partial=True 的 assistant_event;下一轮组装
                    # prompt 时 history 里同条 assistant 文本出现两次 → LLM 自洽地真把回复说两遍。
                    # ToolCallRequest / ToolCallResult:agent 主链路自处理,IM 不感知
            except BaseException as exc:
                self._persist_run_error(conv, exc, None)
                raise
        return "".join(buf)

    def _register_user_alias(
        self,
        provider_or_send: IMOutboundProvider | SendCallback,
        *,
        im_type: str,
        event: InboundEvent,
        session_id: str,
        user_event_id: str,
    ) -> None:
        aliases = self._reference_aliases
        bind_id = str(getattr(provider_or_send, "bind_id", "") or "")
        if aliases is None or not bind_id:
            return
        persisted = self._bridge.persistent_event(session_id, user_event_id)
        if persisted is None or persisted.type != "user_message":
            return
        aliases.put(
            im_type=im_type,
            provider_bind_id=bind_id,
            chat_scope=event.chat_scope,
            chat_id=event.chat_id,
            message_aliases=qq_current_message_aliases(event),
            session_id=session_id,
            event_uuid=user_event_id,
            source_role="user",
        )

    def _register_outbound_alias(
        self,
        provider_or_send: IMOutboundProvider | SendCallback,
        *,
        im_type: str,
        event: InboundEvent,
        session_id: str,
        event_uuid: str,
        provider_message_id: str | None,
        event_uuids: tuple[str, ...] = (),
    ) -> None:
        aliases = self._reference_aliases
        bind_id = str(getattr(provider_or_send, "bind_id", "") or "")
        if aliases is None or not bind_id or not provider_message_id:
            return
        aliases.put(
            im_type=im_type,
            provider_bind_id=bind_id,
            chat_scope=event.chat_scope,
            chat_id=event.chat_id,
            message_aliases=[provider_message_id],
            session_id=session_id,
            event_uuid=event_uuid,
            source_role="assistant",
            event_uuids=event_uuids,
        )

    @staticmethod
    def _open_stream(
        provider_or_send: IMOutboundProvider | SendCallback,
        event: InboundEvent,
        *,
        stream_trace: str,
        run_id: str | None,
        session_id: str,
        latency_started_ms: int,
    ) -> IMStreamSession | None:
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
                session_id=session_id,
                latency_started_ms=latency_started_ms,
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
                reason=err.reason,
                recovery_action=err.recovery_action,
            )
        except Exception:
            logger.warning("IM run_error 持久化失败", exc_info=True)


def _text_len(value: object) -> int:
    return len(value) if isinstance(value, str) else 0


def _attachment_count(event: InboundEvent) -> int:
    attachments = getattr(event, "attachments", None)
    return len(attachments) if isinstance(attachments, list) else 0


def _origin_destination_id(
    provider_or_send: IMOutboundProvider | SendCallback,
    *,
    im_type: str,
    event: InboundEvent,
    session_id: str,
) -> str | None:
    bind_id = getattr(provider_or_send, "bind_id", None)
    provider_type = getattr(provider_or_send, "type", None)
    if not isinstance(bind_id, str) or not bind_id:
        return None
    if provider_type != im_type:
        return None
    return "|".join((im_type, bind_id, event.chat_scope, event.chat_id, session_id))


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
    await relay.flush()
    return relay.full_text
