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
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import portalocker
from portalocker.exceptions import LockException

from .adapters.qq import QQAdapter
from .content import OutboundContent
from .coordinator import IMBindingCoordinator
from .credentials import CredentialStore, ImCredential
from .dedupe import RecentMessageDeduper
from .event import InboundEvent, InboundInteraction
from .inbound_resolver import InboundResolver, InboundResolveResult, NoopInboundResolver
from .provider import IMProvider, ProviderStatus
from .state import ControlCapability, IMBindingKey, IMStateStore

if TYPE_CHECKING:
    from .control import IMControlService
    from .router import IMRouter

__all__ = [
    "IMDesiredOnlineSnapshot",
    "IMProviderAlreadyRegisteredError",
    "IMRuntime",
    "ProviderInfo",
]

logger = logging.getLogger(__name__)


class IMProviderAlreadyRegisteredError(RuntimeError):
    """Raised when add-only onboarding collides with an existing binding."""


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
    binding_status: Literal["unbound", "bound", "invalid"]
    session_id: str | None
    controls_capability: ControlCapability


@dataclass(frozen=True)
class IMDesiredOnlineSnapshot:
    """用户仍要求持续在线的 IM 绑定快照。

    该状态独立于 provider 的瞬时连接健康；断线重连或启动失败不会把已保存绑定
    误判成“不再需要在线”。
    """

    active: bool
    binding_count: int


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
        inbound_resolver: InboundResolver | None = None,
        deduper: RecentMessageDeduper | None = None,
        lock_base_dir: Path | None = None,
        state_store: IMStateStore | None = None,
        coordinator: IMBindingCoordinator | None = None,
        control_service: IMControlService | None = None,
        session_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self._router = router
        self._credentials = credentials
        self._resume_base_dir = resume_base_dir
        self._lock_base_dir = lock_base_dir or resume_base_dir.parent / "im_provider_locks"
        self._inbound_resolver = inbound_resolver or NoopInboundResolver()
        self._deduper = deduper
        self._state = state_store or IMStateStore(resume_base_dir.parent / "im_state.json")
        self._coordinator = coordinator or IMBindingCoordinator()
        self._control_service = control_service
        self._session_exists = session_exists or (lambda _session_id: False)
        self._providers: dict[tuple[str, str], IMProvider] = {}
        self._provider_locks: dict[tuple[str, str], Any] = {}
        self._desired_bindings: set[tuple[str, str]] = set()
        self._desired_bindings_lock = threading.RLock()
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
        with self._desired_bindings_lock:
            self._desired_bindings = {(cred.im_type, _provider_bind_id(cred)) for cred in creds}
        logger.info("IMRuntime starting: %d credential(s) loaded", len(creds))
        for cred in creds:
            try:
                self._reconcile_legacy_binding(cred)
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
        records = [
            (key, provider, self._provider_locks.get(key))
            for key, provider in self._providers.items()
        ]
        self._providers.clear()
        self._provider_locks.clear()

        async def _stop_one(p: IMProvider, lock: Any | None) -> None:
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
            finally:
                self._release_provider_lock(lock, p.type, p.bind_id)

        if records:
            await asyncio.gather(*(_stop_one(p, lock) for _key, p, lock in records))

    # ------------------------------------------------------------------
    # External API(给 routes / onboard 用)
    # ------------------------------------------------------------------

    def register_after_onboard(
        self,
        cred: ImCredential,
        *,
        replace_existing: bool = True,
    ) -> None:
        """onboard 完成后:落盘凭据 + 立即建 provider 启动。

        默认沿用历史行为:如果同 ``(im_type, bind_id)`` 的 provider 已存在,
        **覆盖**旧连接。``replace_existing=False`` 用于安全追加账号:冲突时会在
        凭据落盘和 provider stop 之前抛错,确保原连接完全不受影响。
        """
        key = (cred.im_type, _provider_bind_id(cred))
        if not replace_existing and self._binding_exists(key):
            raise IMProviderAlreadyRegisteredError(
                "IM binding already exists; add-only onboarding refused to replace it: "
                f"im_type={cred.im_type}, bind_id={_mask(key[1])}"
            )

        self._credentials.save(cred)
        with self._desired_bindings_lock:
            self._desired_bindings.add(key)
        old = self._providers.pop(key, None)
        if old is not None:
            # 旧 provider 异步 stop;不 block onboard 调用方
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._stop_one_silent(old))
            self._background_stops.add(task)
            task.add_done_callback(self._background_stops.discard)
        self._spawn_provider(cred)

    def _binding_exists(self, key: tuple[str, str]) -> bool:
        """Check runtime and persisted desired state before an add-only write."""
        if key in self._providers:
            return True
        with self._desired_bindings_lock:
            if key in self._desired_bindings:
                return True
        return any(
            (cred.im_type, _provider_bind_id(cred)) == key for cred in self._credentials.list_all()
        )

    async def unbind(self, im_type: str, bind_id: str) -> bool:
        """解绑:stop provider + 删凭据。

        Returns:
            ``True`` 表示找到并解绑;``False`` 表示原本就未绑定(幂等)。
        """
        key = (im_type, bind_id)
        provider = self._providers.pop(key, None)
        lock = self._provider_locks.pop(key, None)
        if provider is not None:
            try:
                await self._stop_one_silent(provider)
            finally:
                self._release_provider_lock(lock, provider.type, provider.bind_id)
        self._credentials.delete(im_type, bind_id)
        self._state.delete_provider(im_type, bind_id)
        with self._desired_bindings_lock:
            self._desired_bindings.discard(key)
        return provider is not None

    def list_status(self) -> list[ProviderInfo]:
        """provider 列表,给 ``GET /v1/im/providers`` 序列化。

        wire 暴露 raw ``bind_id``(给前端 unbind 用)+ ``bind_id_masked``(展示用)。
        """
        result: list[ProviderInfo] = []
        for provider in self._providers.values():
            key = self._primary_binding_key(provider.type, provider.bind_id)
            binding = self._state.get_binding(key)
            binding_status: Literal["unbound", "bound", "invalid"] = "unbound"
            session_id: str | None = None
            if binding is not None:
                session_id = binding.session_id
                binding_status = "bound" if self._session_exists(session_id) else "invalid"
            result.append(
                ProviderInfo(
                    im_type=provider.type,
                    bind_id=provider.bind_id,
                    bind_id_masked=_mask(provider.bind_id),
                    status=provider.status(),
                    binding_status=binding_status,
                    session_id=session_id,
                    controls_capability=provider.controls_capability(),
                )
            )
        return result

    async def bind_session(self, im_type: str, bind_id: str, session_id: str) -> ProviderInfo:
        """Bind the provider's primary chat to an existing Agent session."""
        provider = self._providers.get((im_type, bind_id))
        if provider is None:
            raise KeyError((im_type, bind_id))
        if not self._session_exists(session_id):
            from agent import SessionNotFoundError

            raise SessionNotFoundError(session_id)
        key = self._primary_binding_key(im_type, bind_id)
        async with self._coordinator.try_mutation(key):
            self._state.set_binding(key, session_id)
        return next(
            info
            for info in self.list_status()
            if info.im_type == im_type and info.bind_id == bind_id
        )

    def desired_online_snapshot(self) -> IMDesiredOnlineSnapshot:
        """返回期望持续在线的绑定数，而非当前成功连接的 provider 数。"""
        with self._desired_bindings_lock:
            count = len(self._desired_bindings)
        return IMDesiredOnlineSnapshot(active=count > 0, binding_count=count)

    def migration_snapshot_credentials(self) -> list[ImCredential]:
        """返回可跨设备重新加密的凭据对象，仅供 owner-only 迁移控制面。"""
        return self._credentials.list_all()

    def migration_snapshot_state(self) -> dict[str, Any]:
        """Return non-secret IM routing/capability state for device migration."""
        return self._state.snapshot()

    async def migration_replace_credentials(
        self,
        credentials: list[ImCredential],
        state: dict[str, Any] | None = None,
    ) -> None:
        """停止 provider、整体替换凭据，再按新机密文重新启动 provider。"""
        await self.stop()
        self._credentials.replace_all(credentials)
        if state is not None:
            self._state.replace(state)
        self.start()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _spawn_provider(self, cred: ImCredential) -> None:
        provider = self._build_provider(cred)
        key = (provider.type, provider.bind_id)
        lock = self._provider_locks.get(key)
        if lock is None:
            lock = self._acquire_provider_lock(provider.type, provider.bind_id)
            self._provider_locks[key] = lock
        on_inbound = self._make_inbound_cb(provider)
        on_interaction = self._make_interaction_cb(provider)
        try:
            provider.start(on_inbound=on_inbound, on_interaction=on_interaction)
        except Exception:
            self._providers.pop(key, None)
            self._provider_locks.pop(key, None)
            self._release_provider_lock(lock, provider.type, provider.bind_id)
            raise
        self._providers[key] = provider
        logger.info(
            "IM provider spawned (im_type=%s, bind_id=%s)",
            provider.type,
            _mask(provider.bind_id),
        )

    def _acquire_provider_lock(self, im_type: str, bind_id: str) -> Any:
        self._lock_base_dir.mkdir(parents=True, exist_ok=True)
        path = self._lock_base_dir / f"{im_type}_{_stable_hash(bind_id)}.lock"
        lock = portalocker.Lock(path, "a", timeout=0)
        try:
            lock.acquire()
        except LockException as e:
            raise RuntimeError(
                f"IM provider already active in another process: im_type={im_type}, "
                f"bind_id={_mask(bind_id)}, lock={path}"
            ) from e
        logger.info(
            "IM provider lock acquired (im_type=%s, bind_id=%s, lock=%s)",
            im_type,
            _mask(bind_id),
            path,
        )
        return lock

    @staticmethod
    def _release_provider_lock(lock: Any | None, im_type: str, bind_id: str) -> None:
        if lock is None:
            return
        try:
            lock.release()
        except Exception:
            logger.warning(
                "IM provider lock release failed (im_type=%s, bind_id=%s)",
                im_type,
                _mask(bind_id),
                exc_info=True,
            )

    def _build_provider(self, cred: ImCredential) -> IMProvider:
        """工厂:按 ``cred.im_type`` 实例化对应 adapter。

        未来扩展:加 ``elif cred.im_type == "feishu": return FeishuAdapter(...)``
        即可,IMRuntime / Router / Routes 零改动。
        """
        if cred.im_type == "qq":
            return QQAdapter(
                cred=cred,
                resume_base_dir=self._resume_base_dir,
                state_store=self._state,
            )
        raise ValueError(f"unsupported IM type: {cred.im_type}")

    def _make_inbound_cb(self, provider: IMProvider) -> Callable[[InboundEvent], Awaitable[None]]:
        """构造 provider 用的 on_inbound 回调:转发到 router。

        闭包绑定 provider 实例,让 router 通过 ``send_fn=lambda c: provider.send(c)``
        回写到正确的 provider(避免多个 provider 同时绑定时回写到错误的 adapter)。
        """

        async def cb(event: InboundEvent) -> None:
            logger.info(
                "IM inbound received im_type=%s provider_bind=%s chat_scope=%s chat_id=%s "
                "message_id=%s content_len=%d stripped_content_len=%d attachment_count=%d "
                "dedupe_enabled=%s",
                provider.type,
                provider.bind_id,
                event.chat_scope,
                event.chat_id,
                event.message_id,
                _text_len(event.content),
                _text_len((event.content or "").strip()),
                _attachment_count(event),
                self._deduper is not None,
            )
            if self._deduper is not None:
                key = self._deduper.key_for(provider.type, event)
                if self._deduper.seen_or_mark(key):
                    logger.info(
                        "IM inbound duplicate ignored (im_type=%s, chat_scope=%s, "
                        "chat_id=%s, message_id=%s, content_len=%d, attachment_count=%d)",
                        provider.type,
                        event.chat_scope,
                        event.chat_id,
                        event.message_id,
                        _text_len(event.content),
                        _attachment_count(event),
                    )
                    return

            binding_key = IMBindingKey(
                im_type=provider.type,
                provider_bind_id=provider.bind_id,
                chat_scope=event.chat_scope,
                chat_id=event.chat_id,
            )
            async with self._coordinator.turn(binding_key):
                await self._handle_bound_inbound(provider, event, binding_key)

        return cb

    async def _handle_bound_inbound(
        self,
        provider: IMProvider,
        event: InboundEvent,
        binding_key: IMBindingKey,
    ) -> None:
        binding = self._state.get_binding(binding_key)
        if binding is None:
            await provider.send(
                OutboundContent(
                    chat_id=event.chat_id,
                    chat_scope=event.chat_scope,
                    text="还没有选择会话，请先在 agent-friend 的“接入 IM”窗口完成选择。",
                    reply_to_message_id=event.message_id,
                )
            )
            return
        if not self._session_exists(binding.session_id):
            await provider.send(
                OutboundContent(
                    chat_id=event.chat_id,
                    chat_scope=event.chat_scope,
                    text="当前绑定的会话已经不存在，请在 agent-friend 中重新选择。",
                    reply_to_message_id=event.message_id,
                )
            )
            return

        result = await self._resolve_inbound(provider, event)
        resolved_event = result.event or event
        logger.info(
            "IM inbound resolver result im_type=%s action=%s reason=%s "
            "message_id=%s resolved_message_id=%s content_len=%d resolved_content_len=%d "
            "resolved_attachment_count=%d",
            provider.type,
            result.action,
            result.reason,
            event.message_id,
            resolved_event.message_id,
            _text_len(event.content),
            _text_len(resolved_event.content),
            _attachment_count(resolved_event),
        )
        if result.action == "ignore":
            logger.info(
                "IM inbound ignored (im_type=%s, reason=%s, message_id=%s, content_len=%d)",
                provider.type,
                result.reason,
                event.message_id,
                _text_len(event.content),
            )
            return
        if result.action == "reply":
            if result.reply is not None:
                logger.info(
                    "IM inbound direct reply im_type=%s reason=%s message_id=%s reply_len=%d",
                    provider.type,
                    result.reason,
                    event.message_id,
                    _text_len(result.reply.text),
                )
                await provider.send(result.reply)
            return

        if not (resolved_event.content or "").strip() and not result.attachments:
            logger.warning(
                "IM inbound empty content ignored after resolver "
                "(im_type=%s, chat_scope=%s, chat_id=%s, message_id=%s, "
                "original_content_len=%d, attachment_count=%d)",
                provider.type,
                resolved_event.chat_scope,
                resolved_event.chat_id,
                resolved_event.message_id,
                _text_len(event.content),
                _attachment_count(resolved_event),
            )
            return

        logger.info(
            "IM inbound routing to router im_type=%s chat_scope=%s chat_id=%s "
            "message_id=%s content_len=%d stripped_content_len=%d attachment_count=%d",
            provider.type,
            resolved_event.chat_scope,
            resolved_event.chat_id,
            resolved_event.message_id,
            _text_len(resolved_event.content),
            _text_len((resolved_event.content or "").strip()),
            _attachment_count(resolved_event),
        )
        await self._router.handle_inbound(
            provider.type,
            resolved_event,
            provider_or_send=provider,
            attachments=result.attachments,
            session_id=binding.session_id,
        )

    def _make_interaction_cb(
        self,
        provider: IMProvider,
    ) -> Callable[[InboundInteraction], Awaitable[None]] | None:
        control_service = self._control_service
        if control_service is None:
            return None

        async def cb(event: InboundInteraction) -> None:
            await control_service.handle(provider, event)

        return cb

    def _reconcile_legacy_binding(self, cred: ImCredential) -> None:
        bind_id = _provider_bind_id(cred)
        key = self._primary_binding_key(cred.im_type, bind_id)
        if self._state.get_binding(key) is not None:
            return
        legacy_session_id = f"im:{cred.im_type}:{bind_id}"
        if self._session_exists(legacy_session_id):
            self._state.set_binding(key, legacy_session_id)

    @staticmethod
    def _primary_binding_key(im_type: str, bind_id: str) -> IMBindingKey:
        return IMBindingKey(
            im_type=im_type,
            provider_bind_id=bind_id,
            chat_scope="c2c",
            chat_id=bind_id,
        )

    async def _resolve_inbound(
        self,
        provider: IMProvider,
        event: InboundEvent,
    ) -> InboundResolveResult:
        try:
            return await self._inbound_resolver.resolve(provider.type, event)
        except Exception:
            logger.exception(
                "IM inbound resolver failed (im_type=%s, chat_scope=%s, message_id=%s)",
                provider.type,
                event.chat_scope,
                event.message_id,
            )
            if (event.content or "").strip():
                return InboundResolveResult.route(event)
            return InboundResolveResult.direct_reply(
                OutboundContent(
                    chat_id=event.chat_id,
                    chat_scope=event.chat_scope,
                    text="这条消息我暂时没处理好，可以再发一次或改发文字。",
                    reply_to_message_id=event.message_id,
                ),
                reason="resolver_error",
            )

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


def _stable_hash(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _provider_bind_id(cred: ImCredential) -> str:
    if cred.im_type == "qq":
        return cred.user_openid or cred.bind_id
    return cred.bind_id


def _text_len(value: object) -> int:
    return len(value) if isinstance(value, str) else 0


def _attachment_count(event: InboundEvent) -> int:
    attachments = getattr(event, "attachments", None)
    return len(attachments) if isinstance(attachments, list) else 0
