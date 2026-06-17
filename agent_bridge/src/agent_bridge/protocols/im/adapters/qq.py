"""QQ adapter(022 起):wrap ``qqbot-agent-sdk`` 4 件套(QQApiClient + QQWebSocket
+ EventParser + WSCallbacks),实现 :class:`agent_bridge.protocols.im.IMProvider`。

设计要点(详见 design.md §3.5):

- **SDK 自带独立 daemon thread + 自动重连 + 心跳 + Resume**:adapter 只提供
  callbacks,**不自己写重连**。
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
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from qqbot_agent_sdk.api_client import QQApiClient
from qqbot_agent_sdk.dto import MessageToCreate
from qqbot_agent_sdk.event_parser import EventParser, InboundEvent
from qqbot_agent_sdk.websocket import QQWebSocket, WSCallbacks

from ..content import OutboundContent
from ..credentials import ImCredential
from ..provider import ProviderStatus

__all__ = ["QQAdapter"]

logger = logging.getLogger(__name__)


_RESUME_FILE_PREFIX = "qq_"
"""resume token 文件命名前缀;路径 = ``<base>/qq_<bind_id_hash[:16]>.json``。"""


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

    def __init__(self, cred: ImCredential, resume_base_dir: Path) -> None:
        self._cred = cred
        self.bind_id: str = cred.user_openid or cred.bind_id
        self._resume_path = self._make_resume_path(resume_base_dir, self.bind_id)

        # 长连组件,start() 时初始化
        self._api: QQApiClient | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._ws: QQWebSocket | None = None
        self._parser = EventParser()
        self._on_inbound: Callable[[InboundEvent], Awaitable[None]] | None = None
        self._status: ProviderStatus = "stopped"
        self._heartbeat_interval: float = 30.0  # SDK 默认值,set_heartbeat_interval 回调更新

    # ------------------------------------------------------------------
    # IMProvider lifecycle
    # ------------------------------------------------------------------

    def start(self, on_inbound: Callable[[InboundEvent], Awaitable[None]]) -> None:
        """启动长连(同步入口;SDK 内部起独立 daemon thread)。

        调用前提:必须在 asyncio event loop 内调用(:func:`asyncio.get_running_loop`
        要求 active loop),通常是 bridge 的 FastAPI lifespan 启动期。
        """
        self._on_inbound = on_inbound

        # httpx async client 由 adapter 持有 + 管理生命周期
        self._http_client = httpx.AsyncClient(timeout=30.0)

        self._api = QQApiClient(
            app_id=self._cred.app_id,
            client_secret=self._cred.client_secret,
            log_tag=f"QQBot:{self.bind_id[:8]}",
        )
        self._api.setup(self._http_client)
        gateway_url = self._api.get_gateway_url_sync()

        callbacks = WSCallbacks(
            on_message_event=self._on_message,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
            on_fatal_error=self._on_fatal_error,
            get_token=self._api.ensure_token_sync,
            get_session=self._load_resume_token,
            set_session=self._save_resume_token,
            set_heartbeat_interval=self._set_heartbeat_interval,
            clear_token=self._api.clear_token,
            fail_pending=self._fail_pending,
            get_gateway_url=self._api.get_gateway_url_sync,
        )
        self._ws = QQWebSocket(callbacks=callbacks, log_tag=f"QQBot:{self.bind_id[:8]}")

        main_loop = asyncio.get_running_loop()
        self._ws.start(gateway_url, main_loop)

    async def send(self, content: OutboundContent) -> None:
        """回写一条 c2c 文本消息给 QQ 平台。

        非零 ``code`` 错误码仅 log warning(43xxx 内容审核 / 11xxx 权限等),
        不抛、不影响后续消息。网络异常也 catch + log,不抛。
        """
        if self._api is None:
            logger.warning("QQAdapter.send called before start (bind=%s)", self.bind_id)
            return

        msg = MessageToCreate(
            content=content.text,
            msg_type=0,  # 0 = 文本
            msg_id=content.reply_to_message_id or "",
        )
        try:
            resp: dict[str, Any] = await self._api.post_c2c_message(content.chat_id, msg)
        except Exception:
            logger.exception(
                "QQ post_c2c_message 网络/HTTP 失败 (bind=%s, chat=%s)",
                self.bind_id,
                content.chat_id,
            )
            return

        code = resp.get("code", 0) if isinstance(resp, dict) else 0
        if code:
            logger.warning(
                "QQ post_c2c_message 非零 code=%s msg=%s (bind=%s, chat=%s)",
                code,
                resp.get("message") if isinstance(resp, dict) else None,
                self.bind_id,
                content.chat_id,
            )

    async def stop(self) -> None:
        """停止长连,释放资源。幂等:重复调用不抛。"""
        ws = self._ws
        self._ws = None
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

    # ------------------------------------------------------------------
    # SDK callbacks
    # ------------------------------------------------------------------

    async def _on_message(self, event_type: str, raw: dict[str, Any]) -> None:
        """SDK 每收一条 dispatch payload → parse → 仅 c2c 转发到 router。"""
        event = self._parser.parse(event_type, raw)
        if event is None:
            return
        if event.chat_scope != "c2c":  # 本期只 c2c
            return
        if self._on_inbound is None:
            logger.warning(
                "QQAdapter received message before start binding (bind=%s)", self.bind_id
            )
            return
        await self._on_inbound(event)

    def _on_connected(self) -> None:
        logger.info("QQ ws connected (bind=%s)", self.bind_id)
        self._set_status("active")

    def _on_disconnected(self) -> None:
        logger.warning("QQ ws disconnected (bind=%s)", self.bind_id)
        self._set_status("degraded")

    def _on_fatal_error(self, code: str, message: str) -> None:
        logger.error("QQ ws fatal: %s %s (bind=%s)", code, message, self.bind_id)
        self._set_status("error")

    def _set_heartbeat_interval(self, seconds: float) -> None:
        """SDK 协商完心跳间隔后调用;v1 我们只存值供调试,不主动驱动心跳(SDK 自管)。"""
        self._heartbeat_interval = seconds

    def _fail_pending(self, reason: str) -> None:
        """SDK 失败所有 pending response futures 的钩子。v1 我们不用 request/response
        pattern(只用 inbound dispatch),no-op 即可。"""
        logger.debug("QQ ws fail_pending: %s (bind=%s)", reason, self.bind_id)

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
