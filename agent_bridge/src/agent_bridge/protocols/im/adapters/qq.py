"""QQ adapter(022 起):wrap ``qqbot-agent-sdk`` 4 件套(QQApiClient + QQWebSocket
+ EventParser + WSCallbacks),实现 :class:`agent_bridge.protocols.im.IMProvider`。

设计要点(详见 design.md §3.5):

- **SDK 自带独立 daemon thread + 连接后的自动重连 + 心跳 + Resume**:adapter 负责
  首次 READY / RESUMED 前的 bootstrap 自愈；连接建立后交回 SDK 管理。
- **Resume token 落简单 json 文件**:SDK 通过 ``WSCallbacks.get_session`` /
  ``set_session`` 读写,我们提供 ``<resume_base_dir>/qq_<bind_id_hash>.json``。
- **错误码兜底**:``post_c2c_message`` 返回 dict 里查 ``code``,非零仅 log warning
  (不抛、不影响后续消息)。43xxx(内容审核)/ 11xxx(权限)等常见错误码就是这个
  路径处理。
- **chat_scope filter**:本期只处理 ``c2c``;``group`` / ``guild`` / ``dm``
  全部 ignore。
- **httpx client 生命周期**:adapter 内部创建 ``httpx.AsyncClient``,start 时
  传给 ``QQApiClient.setup``,stop 时 ``aclose()``。

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.5。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from agent.latency import RunLatencyContext, log_run_latency
from qqbot_agent_sdk.api_client import QQApiClient
from qqbot_agent_sdk.dto import (
    InlineKeyboard,
    KeyboardButton,
    KeyboardButtonAction,
    KeyboardButtonPermission,
    KeyboardButtonRenderData,
    KeyboardContent,
    KeyboardRow,
    MarkdownContent,
    MessageToCreate,
    QQMessageType,
    parse_interaction_event,
)
from qqbot_agent_sdk.event_parser import EventParser, InboundEvent
from qqbot_agent_sdk.websocket import QQWebSocket, WSCallbacks

from ..content import OutboundAction, OutboundContent, OutboundControls
from ..credentials import ImCredential
from ..event import InboundInteraction
from ..provider import OutboundSendResult, ProviderStatus
from ..state import ControlCapability, IMStateStore
from ..streaming import IMStreamSession, OutboundStreamContext
from .qq_streaming import QQC2CStreamSession

__all__ = ["QQAdapter"]

logger = logging.getLogger(__name__)


_RESUME_FILE_PREFIX = "qq_"
"""resume token 文件命名前缀;路径 = ``<base>/qq_<bind_id_hash[:16]>.json``。"""


_CAPABILITY_REPROBE_AFTER = timedelta(hours=24)
_QQ_REUSABLE_CLICK_LIMIT = 1000
_BOOTSTRAP_RETRY_DELAYS = (2.0, 5.0, 10.0, 30.0, 60.0)
_BOOTSTRAP_RETRY_JITTER = 0.2
_BOOTSTRAP_READY_TIMEOUT_SECONDS = 30.0
_TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429})


class _QQInlineKeyboard(InlineKeyboard):
    """SDK keyboard that preserves independent QQ callback buttons."""

    def __init__(
        self,
        content: KeyboardContent,
        reusable_buttons: frozenset[tuple[int, int]],
    ) -> None:
        super().__init__(content=content)
        self._reusable_buttons = reusable_buttons

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        rows = payload["content"]["rows"]
        for row in rows:
            for button in row["buttons"]:
                # The SDK serializes every button into the same ``default`` group.
                # QQ treats callback buttons in a group as mutually exclusive, which
                # makes reusable controls grey and unclickable after the first click.
                button.pop("group_id", None)
        for row_index, button_index in self._reusable_buttons:
            # Keep the explicit multi-click limit while removing the field most
            # likely to force QQ's C2C client into its one-shot visited state.
            rows[row_index]["buttons"][button_index]["render_data"].pop("visited_label", None)
        return payload


class QQAdapter:
    """QQ 官方 Bot OpenAPI · 创建者专属模式 adapter。

    Args:
        cred: 已加密落盘的凭据(app_id / client_secret / user_openid)。
        resume_base_dir: SDK Resume token 落盘根目录。生产 = ``agent.user_data_dir()
            / "im_resume"``;测试注入 tmp 目录。

    实现 :class:`agent_bridge.protocols.im.IMProvider` Protocol,所以**没有 inherit
    declaration** —— Protocol 是 structural typing。
    """

    type: str = "qq"

    def __init__(
        self,
        cred: ImCredential,
        resume_base_dir: Path,
        state_store: IMStateStore | None = None,
    ) -> None:
        self._cred = cred
        self.bind_id: str = cred.user_openid or cred.bind_id
        self._resume_path = self._make_resume_path(resume_base_dir, self.bind_id)
        self._state_store = state_store
        persisted = state_store.capability("qq", self.bind_id) if state_store is not None else None
        self._controls_capability: ControlCapability = (
            persisted.status if persisted is not None else "unknown"
        )
        self._controls_checked_at = persisted.checked_at if persisted is not None else None

        # 长连组件,start() 时初始化
        self._api: QQApiClient | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._ws: QQWebSocket | None = None
        self._ws_generation: object | None = None
        self._startup_task: asyncio.Task[None] | None = None
        self._startup_loop: asyncio.AbstractEventLoop | None = None
        self._startup_signal: asyncio.Event | None = None
        self._parser = EventParser()
        self._on_inbound: Callable[[InboundEvent], Awaitable[None]] | None = None
        self._on_interaction: Callable[[InboundInteraction], Awaitable[None]] | None = None
        self._status: ProviderStatus = "stopped"
        self._heartbeat_interval: float = 30.0  # SDK 默认值,set_heartbeat_interval 回调更新
        self._next_msg_seq_value = int(time.monotonic_ns() % 65_535) or 1

    # ------------------------------------------------------------------
    # IMProvider lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        on_inbound: Callable[[InboundEvent], Awaitable[None]],
        on_interaction: Callable[[InboundInteraction], Awaitable[None]] | None = None,
    ) -> None:
        """启动长连，不让 QQ token / gateway 网络请求阻塞 bridge readiness。

        调用前提:必须在 asyncio event loop 内调用(:func:`asyncio.get_running_loop`
        要求 active loop),通常是 bridge 的 FastAPI lifespan 启动期。同步入口只完成
        本地装配；首次 token / gateway 请求在线程中执行，完成后再拉起 SDK WebSocket。
        """
        self._on_inbound = on_inbound
        self._on_interaction = on_interaction

        # httpx async client 由 adapter 持有 + 管理生命周期
        self._http_client = httpx.AsyncClient(timeout=30.0)

        self._api = QQApiClient(
            app_id=self._cred.app_id,
            client_secret=self._cred.client_secret,
            log_tag=f"QQBot:{self.bind_id[:8]}",
        )
        self._api.setup(self._http_client)

        main_loop = asyncio.get_running_loop()
        self._startup_loop = main_loop
        self._set_status("degraded")
        self._startup_task = main_loop.create_task(self._start_websocket(main_loop, self._api))

    async def _start_websocket(
        self,
        main_loop: asyncio.AbstractEventLoop,
        api: QQApiClient,
    ) -> None:
        attempt = 0
        try:
            while self._api is api:
                signal = asyncio.Event()
                self._startup_signal = signal
                ws: QQWebSocket | None = None
                generation: object | None = None
                try:
                    gateway_url = await asyncio.to_thread(api.get_gateway_url_sync)
                    if self._api is not api:
                        return

                    generation = object()
                    callbacks = self._make_ws_callbacks(api, generation)
                    ws = QQWebSocket(
                        callbacks=callbacks,
                        log_tag=f"QQBot:{self.bind_id[:8]}",
                    )
                    self._ws_generation = generation
                    self._ws = ws
                    ws.start(gateway_url, main_loop)
                    await asyncio.wait_for(
                        signal.wait(),
                        timeout=_BOOTSTRAP_READY_TIMEOUT_SECONDS,
                    )
                    if (
                        self._api is not api
                        or self._ws is not ws
                        or self._ws_generation is not generation
                    ):
                        return
                    if self.status() in {"active", "error"}:
                        return
                    raise TimeoutError("QQ websocket bootstrap ended without a terminal status")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._api is not api:
                        return
                    if _is_permanent_bootstrap_error(exc):
                        if ws is not None and generation is not None:
                            await self._stop_bootstrap_attempt(ws, generation)
                        self._set_status("error")
                        logger.error(
                            "QQ gateway startup failed permanently (bind=%s)",
                            self.bind_id,
                            exc_info=True,
                        )
                        return

                    if ws is not None and generation is not None:
                        await self._stop_bootstrap_attempt(ws, generation)
                    delay = _bootstrap_retry_delay(attempt)
                    attempt += 1
                    self._set_status("degraded")
                    logger.warning(
                        "QQ gateway startup transient failure; retrying "
                        "(bind=%s, attempt=%d, retry_in=%.1fs, error=%s)",
                        self.bind_id,
                        attempt,
                        delay,
                        type(exc).__name__,
                        exc_info=attempt == 1,
                    )
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        finally:
            if self._startup_task is asyncio.current_task():
                self._startup_task = None
                self._startup_signal = None
                self._startup_loop = None

    def _make_ws_callbacks(self, api: QQApiClient, generation: object) -> WSCallbacks:
        return WSCallbacks(
            on_message_event=self._on_message,
            on_connected=lambda: self._on_connected(generation),
            on_disconnected=lambda: self._on_disconnected(generation),
            on_fatal_error=lambda code, message: self._on_fatal_error(generation, code, message),
            get_token=api.ensure_token_sync,
            get_session=self._load_resume_token,
            set_session=self._save_resume_token,
            set_heartbeat_interval=self._set_heartbeat_interval,
            clear_token=api.clear_token,
            fail_pending=self._fail_pending,
            get_gateway_url=api.get_gateway_url_sync,
            on_interaction_event=self._on_interaction_event,
        )

    async def _stop_bootstrap_attempt(self, ws: QQWebSocket, generation: object) -> None:
        if self._ws is ws and self._ws_generation is generation:
            self._ws = None
            self._ws_generation = None
        try:
            await ws.async_stop()
        except Exception:
            logger.warning(
                "QQ bootstrap websocket cleanup failed (bind=%s)",
                self.bind_id,
                exc_info=True,
            )

    async def send(self, content: OutboundContent) -> OutboundSendResult:
        """回写一条 c2c 文本消息给 QQ 平台。

        非零 ``code`` 错误码仅 log warning(43xxx 内容审核 / 11xxx 权限等),
        不抛、不影响后续消息。网络异常也 catch + log,不抛。
        """
        if self._api is None:
            logger.warning("QQAdapter.send called before start (bind=%s)", self.bind_id)
            return OutboundSendResult("failed", error_kind="provider_not_started")

        msg_seq = self._next_msg_seq()
        msg = _message_to_create(content, msg_seq=msg_seq)
        controls = content.controls if self._should_try_controls(content.controls) else None
        keyboard = _keyboard_from_controls(controls) if controls is not None else None
        requested_rows, requested_buttons = _controls_shape(content.controls)
        sent_rows, sent_buttons = _controls_shape(controls)
        wire_group_ids, wire_click_limits, wire_visited_labels = _keyboard_restriction_counts(
            keyboard
        )
        logger.info(
            "QQ outbound send bind=%s chat=%s reply_to_message_id=%s msg_seq=%d "
            "text_len=%d text_format=%s controls_requested_rows=%d "
            "controls_requested_buttons=%d controls_sent_rows=%d controls_sent_buttons=%d "
            "controls_wire_group_ids=%d controls_wire_click_limits=%d "
            "controls_wire_visited_labels=%d",
            self.bind_id,
            content.chat_id,
            content.reply_to_message_id or "",
            msg_seq,
            _text_len(content.text),
            content.text_format,
            requested_rows,
            requested_buttons,
            sent_rows,
            sent_buttons,
            wire_group_ids,
            wire_click_limits,
            wire_visited_labels,
        )
        try:
            resp: dict[str, Any] = await self._api.post_c2c_message(
                content.chat_id,
                msg,
                keyboard=keyboard,
            )
        except Exception as exc:
            if keyboard is not None and _is_controls_rejection(exc):
                logger.warning(
                    "QQ keyboard rejected over HTTP; retrying without controls (bind=%s, chat=%s)",
                    self.bind_id,
                    content.chat_id,
                )
                return await self._retry_without_controls_after_rejection(content)
            logger.exception(
                "QQ post_c2c_message 网络/HTTP 失败 (bind=%s, chat=%s, "
                "reply_to_message_id=%s, msg_seq=%d, text_len=%d, text_format=%s)",
                self.bind_id,
                content.chat_id,
                content.reply_to_message_id or "",
                msg_seq,
                _text_len(content.text),
                content.text_format,
            )
            return OutboundSendResult("unknown", error_kind="network_or_http")

        code = resp.get("code", 0) if isinstance(resp, dict) else 0
        if code:
            logger.warning(
                "QQ post_c2c_message 非零 code=%s msg=%s (bind=%s, chat=%s, "
                "reply_to_message_id=%s, msg_seq=%d, text_len=%d, text_format=%s)",
                code,
                resp.get("message") if isinstance(resp, dict) else None,
                self.bind_id,
                content.chat_id,
                content.reply_to_message_id or "",
                msg_seq,
                _text_len(content.text),
                content.text_format,
            )
            if keyboard is not None and _is_controls_rejection(resp):
                return await self._retry_without_controls_after_rejection(content)
            return OutboundSendResult("failed", error_kind=f"provider_code_{code}")

        if keyboard is not None:
            self._set_controls_capability("supported")

        response_message_id = _response_message_id(resp)
        logger.info(
            "QQ outbound sent bind=%s chat=%s reply_to_message_id=%s msg_seq=%d "
            "response_message_id=%s text_len=%d text_format=%s",
            self.bind_id,
            content.chat_id,
            content.reply_to_message_id or "",
            msg_seq,
            response_message_id,
            _text_len(content.text),
            content.text_format,
        )
        return OutboundSendResult("delivered", provider_message_id=response_message_id or None)

    def open_text_stream(self, context: OutboundStreamContext) -> IMStreamSession | None:
        """Create a QQ native C2C stream using the already configured API client."""

        api = self._api
        if (
            api is None
            or context.chat_scope != "c2c"
            or not context.reply_to_message_id
            or context.text_format not in {"plain_text", "markdown"}
        ):
            return None
        logger.info(
            "QQ stream opened locally stream_trace=%s run_id=%s bind_ref=%s chat_ref=%s",
            context.stream_trace,
            context.run_id or "",
            hashlib.sha256(self.bind_id.encode("utf-8")).hexdigest()[:12],
            hashlib.sha256(context.chat_id.encode("utf-8")).hexdigest()[:12],
        )
        log_run_latency(
            logger,
            (
                RunLatencyContext(
                    run_id=context.run_id,
                    session_id=context.session_id,
                    started_ms=context.latency_started_ms,
                )
                if context.run_id is not None and context.latency_started_ms is not None
                else None
            ),
            "im_stream_open",
            stream_trace=context.stream_trace,
            provider="qq",
        )
        return QQC2CStreamSession(
            api=api,
            bind_id=self.bind_id,
            context=context,
            msg_seq=self._next_msg_seq(),
        )

    async def stop(self) -> None:
        """停止长连,释放资源。幂等:重复调用不抛。"""
        startup_task = self._startup_task
        self._startup_task = None
        if startup_task is not None:
            startup_task.cancel()
            with suppress(asyncio.CancelledError):
                await startup_task

        ws = self._ws
        self._ws = None
        self._ws_generation = None
        if ws is not None:
            try:
                await ws.async_stop()
            except Exception:
                logger.exception("QQ ws.async_stop 失败 (bind=%s)", self.bind_id)

        http_client = self._http_client
        self._http_client = None
        if http_client is not None:
            try:
                await http_client.aclose()
            except Exception:
                logger.exception("QQ http_client.aclose 失败 (bind=%s)", self.bind_id)

        self._api = None
        self._set_status("stopped")

    def status(self) -> ProviderStatus:
        return self._status

    def controls_capability(self) -> ControlCapability:
        if self._controls_capability == "unsupported" and self._controls_checked_at is not None:
            checked_at = self._controls_checked_at
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=UTC)
            if datetime.now(UTC) - checked_at >= _CAPABILITY_REPROBE_AFTER:
                return "unknown"
        return self._controls_capability

    # ------------------------------------------------------------------
    # SDK callbacks
    # ------------------------------------------------------------------

    async def _on_message(self, event_type: str, raw: dict[str, Any]) -> None:
        """SDK 每收一条 dispatch payload → parse → 仅 c2c 转发到 router。"""
        logger.info(
            "QQ inbound raw event_type=%s bind=%s raw_keys=%s raw_message_id=%s "
            "raw_content_len=%d raw_attachment_count=%d",
            event_type,
            self.bind_id,
            _raw_keys(raw),
            _raw_message_id(raw),
            _raw_content_len(raw),
            _raw_attachment_count(raw),
        )
        event = self._parser.parse(event_type, raw)
        if event is None:
            logger.info(
                "QQ inbound parse ignored event_type=%s bind=%s raw_message_id=%s",
                event_type,
                self.bind_id,
                _raw_message_id(raw),
            )
            return
        if event.chat_scope != "c2c":  # 本期只 c2c
            logger.info(
                "QQ inbound non-c2c ignored event_type=%s bind=%s chat_scope=%s "
                "chat_id=%s message_id=%s content_len=%d attachment_count=%d",
                event_type,
                self.bind_id,
                event.chat_scope,
                event.chat_id,
                event.message_id,
                _text_len(event.content),
                _attachment_count(event),
            )
            return
        if self._on_inbound is None:
            logger.warning(
                "QQAdapter received message before start binding (bind=%s)", self.bind_id
            )
            return
        logger.info(
            "QQ inbound parsed event_type=%s bind=%s chat_scope=%s chat_id=%s user_id=%s "
            "message_id=%s content_len=%d stripped_content_len=%d attachment_count=%d",
            event_type,
            self.bind_id,
            event.chat_scope,
            event.chat_id,
            event.user_id,
            event.message_id,
            _text_len(event.content),
            _text_len((event.content or "").strip()),
            _attachment_count(event),
        )
        await self._on_inbound(event)

    async def _on_interaction_event(self, event_type: str, raw: dict[str, Any]) -> None:
        """ACK QQ message-button interactions before forwarding validated controls."""
        api = self._api
        interaction_id = str(raw.get("id", ""))
        if api is not None and interaction_id:
            try:
                await api.acknowledge_interaction(interaction_id, code=0)
            except Exception:
                logger.warning(
                    "QQ interaction ACK failed (bind=%s, interaction=%s)",
                    self.bind_id,
                    interaction_id,
                    exc_info=True,
                )

        parsed = parse_interaction_event(raw)
        if not parsed.is_c2c or parsed.user_openid != self.bind_id:
            logger.warning(
                "QQ interaction operator mismatch ignored (bind=%s, chat=%s, operator=%s)",
                self.bind_id,
                parsed.chat_id,
                parsed.operator_openid,
            )
            return
        if self._on_interaction is None:
            logger.info("QQ interaction ignored without handler (bind=%s)", self.bind_id)
            return
        self._set_controls_capability("supported")
        await self._on_interaction(
            InboundInteraction(
                interaction_id=parsed.id,
                event_type=event_type,
                chat_scope="c2c",
                chat_id=parsed.chat_id,
                operator_id=parsed.operator_openid,
                payload=parsed.data.resolved.button_data,
            )
        )

    def _on_connected(self, generation: object) -> None:
        if self._ws_generation is not generation:
            logger.debug("stale QQ connected callback ignored (bind=%s)", self.bind_id)
            return
        logger.info("QQ ws connected (bind=%s)", self.bind_id)
        self._set_status("active")
        self._notify_startup_waiter()

    def _on_disconnected(self, generation: object) -> None:
        if self._ws_generation is not generation:
            logger.debug("stale QQ disconnected callback ignored (bind=%s)", self.bind_id)
            return
        logger.warning("QQ ws disconnected (bind=%s)", self.bind_id)
        self._set_status("degraded")

    def _on_fatal_error(self, generation: object, code: str, message: str) -> None:
        if self._ws_generation is not generation:
            logger.debug("stale QQ fatal callback ignored (bind=%s)", self.bind_id)
            return
        logger.error("QQ ws fatal: %s %s (bind=%s)", code, message, self.bind_id)
        self._set_status("error")
        self._notify_startup_waiter()

    def _notify_startup_waiter(self) -> None:
        loop = self._startup_loop
        signal = self._startup_signal
        if loop is None or signal is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(signal.set)
        except RuntimeError:
            logger.debug("QQ startup waiter loop already closed (bind=%s)", self.bind_id)

    def _set_heartbeat_interval(self, seconds: float) -> None:
        """SDK 协商完心跳间隔后调用;v1 我们只存值供调试,不主动驱动心跳(SDK 自管)。"""
        self._heartbeat_interval = seconds

    def _fail_pending(self, reason: str) -> None:
        """SDK 失败所有 pending response futures 的钩子。v1 我们不用 request/response
        pattern(只用 inbound dispatch),no-op 即可。"""
        logger.debug("QQ ws fail_pending: %s (bind=%s)", reason, self.bind_id)

    def _next_msg_seq(self) -> int:
        value = self._next_msg_seq_value
        self._next_msg_seq_value = value + 1
        if self._next_msg_seq_value > 65_535:
            self._next_msg_seq_value = 1
        return value

    def _should_try_controls(self, controls: OutboundControls | None) -> bool:
        return controls is not None and self.controls_capability() != "unsupported"

    async def _retry_without_controls_after_rejection(
        self, content: OutboundContent
    ) -> OutboundSendResult:
        api = self._api
        if api is None:
            return OutboundSendResult("failed", error_kind="provider_not_started")
        msg_seq = self._next_msg_seq()
        msg = _message_to_create(content, msg_seq=msg_seq)
        try:
            response = await api.post_c2c_message(content.chat_id, msg)
        except Exception:
            logger.warning(
                "QQ text fallback after keyboard rejection failed (bind=%s, chat=%s)",
                self.bind_id,
                content.chat_id,
                exc_info=True,
            )
            return OutboundSendResult("unknown", error_kind="fallback_network_or_http")
        code = response.get("code", 0) if isinstance(response, dict) else 0
        if code:
            logger.warning(
                "QQ text fallback after keyboard rejection returned code=%s (bind=%s, chat=%s)",
                code,
                self.bind_id,
                content.chat_id,
            )
            return OutboundSendResult("failed", error_kind=f"provider_code_{code}")
        self._set_controls_capability("unsupported")
        return OutboundSendResult(
            "delivered",
            provider_message_id=_response_message_id(response) or None,
        )

    def _set_controls_capability(self, status: ControlCapability) -> None:
        self._controls_capability = status
        self._controls_checked_at = datetime.now(UTC)
        if self._state_store is not None:
            saved = self._state_store.set_capability("qq", self.bind_id, status)
            self._controls_checked_at = saved.checked_at

    # ------------------------------------------------------------------
    # Resume token (SDK get_session/set_session callbacks)
    # ------------------------------------------------------------------

    def _load_resume_token(self) -> tuple[str | None, int | None]:
        """读 resume token;无 / 损坏返回 ``(None, None)``,SDK 走全新 IDENTIFY。"""
        if not self._resume_path.exists():
            return (None, None)
        try:
            data = json.loads(self._resume_path.read_text(encoding="utf-8"))
            return (data.get("session_id"), data.get("last_seq"))
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "QQ resume token unreadable at %s, fresh identify",
                self._resume_path,
            )
            return (None, None)

    def _save_resume_token(self, session_id: str | None, last_seq: int | None) -> None:
        """SDK READY/RESUME 后写 resume token。落盘失败仅 log,不阻塞 SDK。

        SDK 协议允许 ``(None, None)``(表示清除),这里也支持:写入 ``null`` 值
        让后续 :meth:`_load_resume_token` 读回相同的 None pair → SDK 走全新 IDENTIFY。
        """
        try:
            self._resume_path.parent.mkdir(parents=True, exist_ok=True)
            self._resume_path.write_text(
                json.dumps({"session_id": session_id, "last_seq": last_seq}),
                encoding="utf-8",
            )
        except OSError:
            logger.exception(
                "QQ resume token save 失败 (bind=%s, path=%s)",
                self.bind_id,
                self._resume_path,
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _set_status(self, s: ProviderStatus) -> None:
        self._status = s

    @staticmethod
    def _make_resume_path(base: Path, bind_id: str) -> Path:
        """``<base>/qq_<sha256(bind_id)[:16]>.json``,跟 credentials 同款规则。"""
        h = hashlib.sha256(bind_id.encode("utf-8")).hexdigest()[:16]
        return Path(base) / f"{_RESUME_FILE_PREFIX}{h}.json"


def _text_len(value: object) -> int:
    return len(value) if isinstance(value, str) else 0


def _bootstrap_retry_delay(attempt: int) -> float:
    base = _BOOTSTRAP_RETRY_DELAYS[min(attempt, len(_BOOTSTRAP_RETRY_DELAYS) - 1)]
    return base * random.uniform(
        1.0 - _BOOTSTRAP_RETRY_JITTER,
        1.0 + _BOOTSTRAP_RETRY_JITTER,
    )


def _is_permanent_bootstrap_error(exc: BaseException) -> bool:
    """Only terminal HTTP client errors stop desired-online bootstrap retries."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            status_code = current.response.status_code
            return 400 <= status_code < 500 and status_code not in _TRANSIENT_HTTP_STATUS_CODES
        current = current.__cause__ or current.__context__
    return False


def _response_message_id(response: object) -> str:
    """只提取平台回执 ID，不记录响应正文或其他潜在敏感字段。"""
    if not isinstance(response, dict):
        return ""
    for key in ("id", "message_id", "msg_id"):
        value = response.get(key)
        if isinstance(value, (str, int)):
            return str(value)
    return ""


def _is_controls_rejection(response: object) -> bool:
    """Only retry when QQ explicitly attributes rejection to keyboard capability."""
    message = (
        str(response.get("message", "")) if isinstance(response, dict) else str(response)
    ).lower()
    return any(marker in message for marker in ("keyboard", "group_id", "按钮", "按键", "权限"))


def _message_to_create(content: OutboundContent, *, msg_seq: int) -> MessageToCreate:
    if content.text_format == "markdown":
        return MessageToCreate(
            msg_type=QQMessageType.MARKDOWN,
            markdown=MarkdownContent(content=content.text),
            msg_id=content.reply_to_message_id or "",
            msg_seq=msg_seq,
        )
    return MessageToCreate(
        content=content.text,
        msg_type=QQMessageType.TEXT,
        msg_id=content.reply_to_message_id or "",
        msg_seq=msg_seq,
    )


def _keyboard_from_controls(controls: OutboundControls | None) -> InlineKeyboard | None:
    if controls is None:
        return None
    rows: list[KeyboardRow] = []
    reusable_buttons: set[tuple[int, int]] = set()
    for row_index, row in enumerate(controls.rows):
        buttons: list[KeyboardButton] = []
        for button_index, action in enumerate(row):
            if action.click_limit is None:
                reusable_buttons.add((row_index, button_index))
            buttons.append(
                KeyboardButton(
                    id=_qq_button_id(action, row_index=row_index, button_index=button_index),
                    render_data=KeyboardButtonRenderData(
                        label=action.label,
                        visited_label=action.visited_label,
                        style=1 if action.style == "primary" else 0,
                    ),
                    action=KeyboardButtonAction(
                        type=1,
                        data=action.payload,
                        permission=KeyboardButtonPermission(type=2),
                        # QQ's C2C callback client currently treats an omitted limit
                        # as single-use. Tencent's multi-click example sends an
                        # explicit finite value, so map our reusable default to a
                        # generously high practical limit.
                        click_limit=(
                            action.click_limit
                            if action.click_limit is not None
                            else _QQ_REUSABLE_CLICK_LIMIT
                        ),
                    ),
                )
            )
        rows.append(KeyboardRow(buttons=buttons))
    return _QQInlineKeyboard(
        content=KeyboardContent(rows=rows),
        reusable_buttons=frozenset(reusable_buttons),
    )


def _controls_shape(controls: OutboundControls | None) -> tuple[int, int]:
    """Return non-sensitive control counts for persisted outbound diagnostics."""
    if controls is None:
        return 0, 0
    return len(controls.rows), sum(len(row) for row in controls.rows)


def _keyboard_restriction_counts(keyboard: InlineKeyboard | None) -> tuple[int, int, int]:
    """Count restrictive wire fields without logging labels or callback payloads."""
    if keyboard is None:
        return 0, 0, 0
    rows = keyboard.to_dict()["content"]["rows"]
    buttons = [button for row in rows for button in row["buttons"]]
    group_ids = sum("group_id" in button for button in buttons)
    click_limits = sum("click_limit" in button["action"] for button in buttons)
    visited_labels = sum("visited_label" in button["render_data"] for button in buttons)
    return group_ids, click_limits, visited_labels


def _qq_button_id(
    action: OutboundAction,
    *,
    row_index: int,
    button_index: int,
) -> str:
    """Map platform-neutral IDs to short, legal, per-keyboard-unique QQ IDs.

    QQ rejects an entire keyboard when IDs contain unsupported characters or collide.
    Callback semantics live in ``action.payload``, so the wire ID can be a safe digest.
    Row/button coordinates ensure duplicate upstream actions still map differently.
    """
    material = f"{row_index}:{button_index}:{action.id}:{action.payload}".encode()
    return f"af-{hashlib.sha256(material).hexdigest()[:16]}"


def _attachment_count(event: InboundEvent) -> int:
    attachments = getattr(event, "attachments", None)
    return len(attachments) if isinstance(attachments, list) else 0


def _raw_keys(raw: dict[str, Any]) -> str:
    return ",".join(sorted(str(k) for k in raw))


def _raw_data(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("d")
    return data if isinstance(data, dict) else raw


def _raw_message_id(raw: dict[str, Any]) -> str:
    data = _raw_data(raw)
    for key in ("id", "msg_id", "message_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _raw_content_len(raw: dict[str, Any]) -> int:
    data = _raw_data(raw)
    return _text_len(data.get("content"))


def _raw_attachment_count(raw: dict[str, Any]) -> int:
    data = _raw_data(raw)
    attachments = data.get("attachments")
    return len(attachments) if isinstance(attachments, list) else 0
