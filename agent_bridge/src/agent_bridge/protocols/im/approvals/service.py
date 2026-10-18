"""Host-owned QQ approval routing; the shared broker remains authoritative."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from uuid import uuid4

from agent.approvals import ApprovalBroker, ApprovalModeConflictError

from ..content import OutboundAction, OutboundContent, OutboundControls
from ..delivery.recovery import send_observed
from ..event import InboundEvent, InboundInteraction
from ..provider import IMProvider, OutboundSendResult
from ..state import IMBindingKey, IMSessionBinding, IMStateStore
from .rendering import RISK_TEXT, approval_text, bounded, markdown_literal, mode_label
from .tokens import TOKEN_PREFIX, Action, ActionKind, ApprovalTokens

logger = logging.getLogger(__name__)
PAGE_PAYLOAD = "af:approval:menu:v1"
_STALE = "这项授权已处理、未送达或已失效，请重新打开菜单或查看桌面原会话。"


@dataclass
class _Route:
    binding: IMSessionBinding
    provider: IMProvider
    run_id: str
    reply_id: str
    loop: asyncio.AbstractEventLoop
    cancel: Callable[[], object] | None = None
    reply_event_id: str | None = None


class IMApprovalService:
    def __init__(
        self,
        broker: ApprovalBroker,
        state: IMStateStore,
        *,
        session_title: Callable[[str], str],
        session_exists: Callable[[str], bool],
    ) -> None:
        self.broker = broker
        self._state = state
        self._title = session_title
        self._exists = session_exists
        self._tokens = ApprovalTokens()
        self._lock = threading.RLock()
        self._providers: dict[tuple[str, str], IMProvider] = {}
        self._routes: dict[tuple[str, str], _Route] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._announced: dict[str, tuple[str, str]] = {}
        self._local_decisions: set[str] = set()
        broker.add_listener(self._on_approval)
        state.add_binding_listener(self._binding_changed)

    def activate(self, provider: IMProvider) -> None:
        key = (provider.type, provider.bind_id)
        with self._lock:
            old = self._providers.get(key)
        if old is not None and old is not provider:
            self.deactivate(old)
        with self._lock:
            self._providers[key] = provider

    def deactivate(self, provider: IMProvider) -> None:
        with self._lock:
            key = (provider.type, provider.bind_id)
            if self._providers.get(key) is provider:
                del self._providers[key]
            routes = [r for r in self._routes.values() if r.provider is provider]
        self._tokens.invalidate(lambda action: action.provider is provider)
        for route in routes:
            self._close_route(route, cancel=True)

    def _active(self, provider: IMProvider) -> bool:
        with self._lock:
            return self._providers.get((provider.type, provider.bind_id)) is provider

    def _binding_changed(self, key: IMBindingKey) -> None:
        self._tokens.invalidate(lambda action: action.binding.key == key)
        with self._lock:
            routes = [r for r in self._routes.values() if r.binding.key == key]
        for route in routes:
            self._close_route(route, cancel=True)

    @contextmanager
    def run_route(
        self, provider: IMProvider, event: InboundEvent, session_id: str, run_id: str
    ) -> Iterator[bool]:
        key = IMBindingKey(provider.type, provider.bind_id, event.chat_scope, event.chat_id)
        binding = self._state.get_binding(key)
        route: _Route | None = None
        if (
            provider.type == "qq"
            and event.chat_scope == "c2c"
            and event.user_id == event.chat_id == provider.bind_id
            and binding is not None
            and binding.session_id == session_id
            and run_id
        ):
            with self._state.binding_guard(binding) as matches:
                if matches and self._active(provider) and self._exists(session_id):
                    route = _Route(
                        binding, provider, run_id, event.message_id, asyncio.get_running_loop()
                    )
                    with self._lock:
                        self.broker.register_surface_run(session_id, run_id, "im:qq")
                        self._routes[(session_id, run_id)] = route
        try:
            yield route is not None
        except BaseException:
            if route is not None:
                self._close_route(route, cancel=True)
            raise
        finally:
            if route is not None:
                self._close_route(route, cancel=False)

    def attach_cancel(self, session_id: str, run_id: str, cancel: Callable[[], object]) -> None:
        with self._lock:
            route = self._routes.get((session_id, run_id))
            if route is not None:
                route.cancel = cancel
                return
        cancel()

    def _close_route(self, route: _Route, *, cancel: bool) -> None:
        session_id = route.binding.session_id
        with self._lock:
            if self._routes.get((session_id, route.run_id)) is not route:
                return
            del self._routes[(session_id, route.run_id)]
        if cancel and route.cancel is not None:
            route.cancel()
        self.broker.unregister_surface_run(session_id, route.run_id)
        self._tokens.invalidate(lambda action: action.run_id == route.run_id)
        with self._lock:
            self._announced = {
                request_id: key
                for request_id, key in self._announced.items()
                if key != (session_id, route.run_id)
            }

    def mode_summary(self, session_id: str) -> str:
        return mode_label(self.broker.mode_state(session_id).mode)

    async def handle(self, provider: IMProvider, event: InboundInteraction) -> bool:
        if event.payload != PAGE_PAYLOAD and not event.payload.startswith(TOKEN_PREFIX):
            return False
        if (
            provider.type != "qq"
            or event.chat_scope != "c2c"
            or event.operator_id != event.chat_id
            or event.chat_id != provider.bind_id
            or not self._active(provider)
        ):
            return True
        key = IMBindingKey(provider.type, provider.bind_id, event.chat_scope, event.chat_id)
        if event.payload == PAGE_PAYLOAD:
            await self.show_page(provider, key, event_id=event.reply_event_id)
            return True
        action = self._tokens.resolve(event.payload)
        if action is None or action.provider is not provider or action.binding.key != key:
            await self._notice(provider, key, _STALE, event_id=event.reply_event_id)
            return True
        outcome = _STALE
        next_page: ActionKind | None = None
        with self._state.binding_guard(action.binding) as matches, self._lock:
            if matches and self._active(provider) and self._exists(action.binding.session_id):
                consumed = self._tokens.consume(event.payload)
                if consumed is not None:
                    for route in self._routes.values():
                        if route.binding.key == key and route.provider is provider:
                            route.reply_event_id = event.reply_event_id
                    if action.kind in {"page", "enable"}:
                        next_page = action.kind
                    elif action.kind == "cancel":
                        outcome = "已取消，本次没有变更授权档位。"
                    elif action.kind in {"confirm", "manual"}:
                        outcome = self._set_mode(action)
                    else:
                        outcome = self._decide(action)
        logger.info(
            "IM approval interaction interaction=%s session_id=%s run_id=%s request_id=%s action=%s",
            event.interaction_id,
            action.binding.session_id,
            action.run_id,
            action.request_id,
            action.kind,
        )
        if next_page == "page":
            await self.show_page(provider, key, event_id=event.reply_event_id)
        elif next_page == "enable":
            await self.show_page(
                provider, key, confirm=True, expected=action.binding, event_id=event.reply_event_id
            )
        else:
            await self._notice(provider, key, outcome, event_id=event.reply_event_id)
        return True

    def _set_mode(self, action: Action) -> str:
        try:
            state = self.broker.set_session_mode(
                action.binding.session_id,
                mode="session_auto" if action.kind == "confirm" else "per_request",
                expected_version=action.mode_version,
            )
        except ApprovalModeConflictError as exc:
            return f"档位已经变化：{mode_label(exc.current.mode)}。请重新打开授权设置。"
        except (KeyError, RuntimeError):
            return "未能变更授权档位，请重新打开菜单检查当前状态。"
        return f"会话授权已设为：{mode_label(state.mode)}。桌面与 QQ 使用同一会话设置。"

    def _decide(self, action: Action) -> str:
        session_id = action.binding.session_id
        with self._lock:
            route = self._routes.get((session_id, action.run_id))
        if route is None or route.provider is not action.provider:
            return _STALE
        pending = next(
            (
                request
                for request in self.broker.pending(session_id=session_id, run_id=action.run_id)
                if request.request_id == action.request_id
            ),
            None,
        )
        if pending is None:
            return _STALE
        decision = (
            pending.fully_approved_decision()
            if action.kind == "allow"
            else pending.cancelled_decision()
        )
        self._local_decisions.add(action.request_id)
        try:
            accepted = self.broker.decide(
                action.request_id,
                decision,
                session_id=session_id,
                run_id=action.run_id,
                expected_kind=action.request_kind,
                expected_version=action.request_version,
            )
        finally:
            self._local_decisions.discard(action.request_id)
        if not accepted:
            return _STALE
        return (
            "已批准该请求，等待执行结果。"
            if action.kind == "allow"
            else "已拒绝该请求，不会执行该项操作。"
        )

    async def show_page(
        self,
        provider: IMProvider,
        key: IMBindingKey,
        *,
        confirm: bool = False,
        expected: IMSessionBinding | None = None,
        event_id: str | None = None,
    ) -> None:
        binding = self._state.get_binding(key)
        if binding is None or not self._exists(binding.session_id):
            await self._notice(
                provider, key, "当前没有有效绑定会话，请在桌面重新选择。", event_id=event_id
            )
            return
        if expected is not None and (
            binding.session_id != expected.session_id or binding.updated_at != expected.updated_at
        ):
            await self._notice(provider, key, _STALE, event_id=event_id)
            return
        mode = self.broker.mode_state(binding.session_id)
        group = uuid4().hex
        action = Action("page", binding, provider, group, mode_version=mode.version)
        try:
            text = markdown_literal(
                f"会话：{self._title(binding.session_id)}\n当前档位：{mode_label(mode.mode)}\n"
            )
            if confirm:
                text += RISK_TEXT
                options: list[tuple[ActionKind, str]] = [
                    ("cancel", "取消"),
                    ("confirm", "确认开启"),
                ]
            else:
                options = [("manual", "每次授权"), ("enable", "本会话免授权")]
            content = OutboundContent(
                chat_id=key.chat_id,
                chat_scope=key.chat_scope,
                text=bounded(text),
                text_format="markdown",
                controls=self._buttons(action, options),
                reply_to_event_id=event_id,
            )
        except (ValueError, KeyError):
            await self._notice(
                provider, key, "授权详情无法完整显示，请在桌面原会话设置。", event_id=event_id
            )
            return
        await self._send_controls(provider, binding, content, group)

    def _buttons(self, action: Action, options: list[tuple[ActionKind, str]]) -> OutboundControls:
        buttons = tuple(
            OutboundAction(
                id=f"approval-{kind}",
                label=label,
                visited_label="已点击",
                payload=self._tokens.issue(replace(action, kind=kind)),
            )
            for kind, label in options
        )
        return OutboundControls(rows=tuple(buttons[i : i + 2] for i in range(0, len(buttons), 2)))

    async def _send_controls(
        self,
        provider: IMProvider,
        binding: IMSessionBinding,
        content: OutboundContent,
        group: str,
        request_id: str = "",
    ) -> None:
        receipt = await self._send(provider, content)
        with self._state.binding_guard(binding) as matches:
            accepted = (
                matches
                and self._active(provider)
                and self._exists(binding.session_id)
                and receipt.outcome == "delivered"
                and bool(receipt.provider_message_id)
                and receipt.controls == "accepted"
            )
            if accepted:
                self._tokens.delivered(group)
            else:
                self._tokens.invalidate(lambda action: action.group == group)
        logger.info(
            "IM approval controls session_id=%s request_id=%s group=%s outcome=%s controls=%s message_id=%s",
            binding.session_id,
            request_id,
            group,
            receipt.outcome,
            receipt.controls,
            receipt.provider_message_id or "",
        )
        if not accepted and self._active(provider):
            await self._notice(
                provider,
                binding.key,
                "QQ 授权按钮未确认送达，本次未授权。请在桌面原会话处理；待审批请求约 5 分钟后失效。",
                event_id=content.reply_to_event_id,
                reply_id=content.reply_to_message_id,
            )

    def _on_approval(self, snapshot: dict[str, object]) -> None:
        key = (str(snapshot["session_id"]), str(snapshot["run_id"]))
        request_id = str(snapshot["request_id"])
        if snapshot.get("status") != "pending":
            self._tokens.invalidate(lambda action: action.request_id == request_id)
        with self._lock:
            route = self._routes.get(key)
            if route is not None and snapshot.get("status") == "pending":
                self._announced[request_id] = key
            elif self._announced.pop(request_id, None) is None:
                return
        if route is None:
            return
        logger.info(
            "IM approval state session_id=%s run_id=%s request_id=%s tool_call_id=%s "
            "version=%s status=%s decision_source=%s",
            key[0],
            key[1],
            request_id,
            snapshot.get("tool_call_id"),
            snapshot.get("version"),
            snapshot.get("status"),
            snapshot.get("decision_source"),
        )
        if request_id in self._local_decisions:
            return  # handle() sends one correlated result for this QQ decision.
        try:
            route.loop.call_soon_threadsafe(self._schedule, route, snapshot)
        except RuntimeError:
            logger.info("IM approval delivery loop unavailable request_id=%s", request_id)

    def _schedule(self, route: _Route, snapshot: dict[str, object]) -> None:
        task = asyncio.create_task(self._deliver_approval(route, snapshot))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "IM approval delivery failed error_type=%s", type(task.exception()).__name__
            )

    async def _deliver_approval(self, route: _Route, snapshot: dict[str, object]) -> None:
        binding = route.binding
        with self._state.binding_guard(binding) as matches:
            if (
                not matches
                or not self._active(route.provider)
                or not self._exists(binding.session_id)
            ):
                return
        request_id = str(snapshot["request_id"])
        status = str(snapshot["status"])
        if status != "pending":
            labels = {
                "approved": "已批准，等待执行结果",
                "denied": "已拒绝",
                "expired": "已过期",
                "cancelled": "已取消",
            }
            await self._notice(
                route.provider,
                binding.key,
                f"授权请求 {request_id}：{labels.get(status, status)}。历史按钮已失效。",
                reply_id=None if route.reply_event_id else route.reply_id,
                event_id=route.reply_event_id,
            )
            return
        if not any(
            r.request_id == request_id
            for r in self.broker.pending(session_id=binding.session_id, run_id=route.run_id)
        ):
            return
        group = uuid4().hex
        action = Action(
            "allow",
            binding,
            route.provider,
            group,
            run_id=route.run_id,
            request_id=request_id,
            request_kind=str(snapshot["kind"]),
            request_version=int(str(snapshot["version"])),
        )
        try:
            text = approval_text(snapshot, self._title(binding.session_id))
        except (KeyError, ValueError, TypeError):
            await self._notice(
                route.provider,
                binding.key,
                f"授权请求 {request_id} 的完整详情无法在 QQ 展示，请在桌面原会话处理。约 5 分钟后失效。",
                reply_id=None if route.reply_event_id else route.reply_id,
                event_id=route.reply_event_id,
            )
            return
        content = OutboundContent(
            chat_id=binding.key.chat_id,
            chat_scope=binding.key.chat_scope,
            reply_to_message_id=None if route.reply_event_id else route.reply_id,
            reply_to_event_id=route.reply_event_id,
            text=text,
            text_format="markdown",
            controls=self._buttons(
                action, [("allow", "仅本次允许"), ("deny", "拒绝"), ("page", "授权设置")]
            ),
        )
        await self._send_controls(route.provider, binding, content, group, request_id)
        if not any(
            r.request_id == request_id
            for r in self.broker.pending(session_id=binding.session_id, run_id=route.run_id)
        ):
            self._tokens.invalidate(lambda item: item.request_id == request_id)
            await self._notice(
                route.provider,
                binding.key,
                f"请求 {request_id} 已不再等待授权，请忽略这张旧卡片。",
                reply_id=None if route.reply_event_id else route.reply_id,
                event_id=route.reply_event_id,
            )

    async def _notice(
        self,
        provider: IMProvider,
        key: IMBindingKey,
        text: str,
        *,
        reply_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        await self._send(
            provider,
            OutboundContent(
                chat_id=key.chat_id,
                chat_scope=key.chat_scope,
                text=text,
                reply_to_message_id=reply_id,
                reply_to_event_id=event_id,
            ),
        )

    @staticmethod
    async def _send(provider: IMProvider, content: OutboundContent) -> OutboundSendResult:
        try:
            return await asyncio.wait_for(send_observed(provider.send, content), timeout=15.0)
        except TimeoutError:
            return OutboundSendResult("unknown", error_kind="approval_delivery_timeout")
