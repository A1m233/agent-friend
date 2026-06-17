"""IM Runtime(022 起):进程级 IM 长连管理者。

跟 :class:`agent.runtime.AgentRuntime` 同构:在 :class:`agent_bridge.assembly.build_runtime`
装配,挂到 :attr:`BridgeRuntime.im_runtime`,FastAPI lifespan 启动期调
:meth:`IMRuntime.start`,退出期调 :meth:`IMRuntime.stop`。

职责:

- 启动时从 :class:`CredentialStore` 加载所有已绑定凭据,逐个建 :class:`IMProvider`
  并启动
- 持有 (im_type, bind_id) → IMProvider 的 dict
- onboard 完成后通过 :meth:`register_after_onboard` 即时启动新 provider(不需要重启
  bridge)
- :meth:`unbind` 停止 provider + 删凭据
- :meth:`list_status` 给 ``GET /v1/im/providers`` 返回脱敏的绑定列表

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.4。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .adapters.qq import QQAdapter
from .credentials import CredentialStore, ImCredential
from .event import InboundEvent
from .provider import IMProvider, ProviderStatus

if TYPE_CHECKING:
    from .router import IMRouter

__all__ = ["IMRuntime", "ProviderInfo"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderInfo:
    """:meth:`IMRuntime.list_status` 返回的 provider 状态(给 GET /v1/im/providers)。

    Attributes:
        im_type: 平台标识
        bind_id: 原始绑定 id(QQ = user_openid);前端 DELETE /v1/im/providers/...
            需要用 raw id 调用,所以 wire 上暴露 raw,UI 自己脱敏展示。openid 本身
            不算敏感(QQ 内部 id,不是 phone/email)。
        bind_id_masked: 脱敏(头 4 字符 + ... + 尾 4 字符)便利字段,前端可直接渲染。
        status: 当前通道状态。
    """

    im_type: str
    bind_id: str
    bind_id_masked: str
    status: ProviderStatus


class IMRuntime:
    """进程级 IM 长连管理者。

    Args:
        router: :class:`IMRouter` 实例(IM ↔ agent 的转发层)。
        credentials: 凭据存储,启动时 ``list_all()`` 加载所有已绑定凭据。
        resume_base_dir: 透传给 :class:`QQAdapter`(SDK Resume token 落盘根目录)。
    """

    def __init__(
        self,
        router: IMRouter,
        credentials: CredentialStore,
        resume_base_dir: Path,
    ) -> None:
        self._router = router
        self._credentials = credentials
        self._resume_base_dir = resume_base_dir
        self._providers: dict[tuple[str, str], IMProvider] = {}
        # 持有 fire-and-forget stop tasks 引用,防 GC 提前回收(RUF006)
        self._background_stops: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """加载所有已绑定凭据 + 启动每个 provider 长连。

        调用前提:必须在 asyncio event loop 内调用(provider 内部用
        :func:`asyncio.get_running_loop`)。
        """
        creds = self._credentials.list_all()
        logger.info("IMRuntime starting: %d credential(s) loaded", len(creds))
        for cred in creds:
            try:
                self._spawn_provider(cred)
            except Exception:
                logger.exception(
                    "IMRuntime: failed to spawn provider (im_type=%s, bind_id=%s)",
                    cred.im_type,
                    cred.bind_id,
                )

    async def stop(self, timeout: float = 5.0) -> None:
        """并发停止所有 provider;每个 provider 各自 ``timeout`` 秒兜底。

        幂等:无 provider 时静默 return。
        """
        providers = list(self._providers.values())
        self._providers.clear()

        async def _stop_one(p: IMProvider) -> None:
            try:
                await asyncio.wait_for(p.stop(), timeout=timeout)
            except TimeoutError:
                logger.warning(
                    "IM provider stop timeout (im_type=%s, bind_id=%s)",
                    p.type,
                    p.bind_id,
                )
            except Exception:
                logger.exception(
                    "IM provider stop failed (im_type=%s, bind_id=%s)",
                    p.type,
                    p.bind_id,
                )

        if providers:
            await asyncio.gather(*(_stop_one(p) for p in providers))

    # ------------------------------------------------------------------
    # External API(给 routes / onboard 用)
    # ------------------------------------------------------------------

    def register_after_onboard(self, cred: ImCredential) -> None:
        """onboard 完成后:落盘凭据 + 立即建 provider 启动。

        如果同 ``(im_type, bind_id)`` 的 provider 已存在,**覆盖**:先 schedule
        旧的 stop(异步,不阻塞当前调用),再启新的。
        """
        self._credentials.save(cred)
        key = (cred.im_type, cred.bind_id)
        old = self._providers.pop(key, None)
        if old is not None:
            # 旧 provider 异步 stop;不 block onboard 调用方
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._stop_one_silent(old))
            self._background_stops.add(task)
            task.add_done_callback(self._background_stops.discard)
        self._spawn_provider(cred)

    async def unbind(self, im_type: str, bind_id: str) -> bool:
        """解绑:stop provider + 删凭据。

        Returns:
            ``True`` 表示找到并解绑;``False`` 表示原本就未绑定(幂等)。
        """
        key = (im_type, bind_id)
        provider = self._providers.pop(key, None)
        if provider is not None:
            await self._stop_one_silent(provider)
        self._credentials.delete(im_type, bind_id)
        return provider is not None

    def list_status(self) -> list[ProviderInfo]:
        """provider 列表,给 ``GET /v1/im/providers`` 序列化。

        wire 暴露 raw ``bind_id``(给前端 unbind 用)+ ``bind_id_masked``(展示用)。
        """
        return [
            ProviderInfo(
                im_type=p.type,
                bind_id=p.bind_id,
                bind_id_masked=_mask(p.bind_id),
                status=p.status(),
            )
            for p in self._providers.values()
        ]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _spawn_provider(self, cred: ImCredential) -> None:
        provider = self._build_provider(cred)
        on_inbound = self._make_inbound_cb(provider)
        provider.start(on_inbound=on_inbound)
        self._providers[(provider.type, provider.bind_id)] = provider
        logger.info(
            "IM provider spawned (im_type=%s, bind_id=%s)",
            provider.type,
            _mask(provider.bind_id),
        )

    def _build_provider(self, cred: ImCredential) -> IMProvider:
        """工厂:按 ``cred.im_type`` 实例化对应 adapter。

        未来扩展:加 ``elif cred.im_type == "feishu": return FeishuAdapter(...)``
        即可,IMRuntime / Router / Routes 零改动。
        """
        if cred.im_type == "qq":
            return QQAdapter(cred=cred, resume_base_dir=self._resume_base_dir)
        raise ValueError(f"unsupported IM type: {cred.im_type}")

    def _make_inbound_cb(self, provider: IMProvider) -> Callable[[InboundEvent], Awaitable[None]]:
        """构造 provider 用的 on_inbound 回调:转发到 router。

        闭包绑定 provider 实例,让 router 通过 ``send_fn=lambda c: provider.send(c)``
        回写到正确的 provider(避免多个 provider 同时绑定时回写到错误的 adapter)。
        """

        async def cb(event: InboundEvent) -> None:
            await self._router.handle_inbound(
                provider.type,
                event,
                send_fn=provider.send,
            )

        return cb

    @staticmethod
    async def _stop_one_silent(p: IMProvider) -> None:
        try:
            await p.stop()
        except Exception:
            logger.exception(
                "IM provider silent stop failed (im_type=%s, bind_id=%s)",
                p.type,
                p.bind_id,
            )


def _mask(s: str) -> str:
    """脱敏 bind_id:头 4 字符 + ... + 尾 4 字符;短串走全保留。"""
    if len(s) <= 8:
        return s
    return f"{s[:4]}...{s[-4:]}"
