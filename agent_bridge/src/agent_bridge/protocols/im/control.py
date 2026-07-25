"""IM-only menu interactions for session and model switching."""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal

from agent import Session, SessionManager, SessionNotFoundError, SessionSummary
from llm_providers import public_model_catalog

from ...errors import map_exception
from ...session_models import SessionModelService
from .content import OutboundAction, OutboundContent, OutboundControls
from .coordinator import IMBindingBusyError, IMBindingCoordinator
from .event import InboundInteraction
from .provider import IMProvider
from .state import IMBindingKey, IMStateStore

__all__ = ["MENU_PAYLOAD", "IMControlService", "menu_controls"]

MENU_PAYLOAD = "af:menu:v1"
_TOKEN_PREFIX = "af:ctl:v1:"
_PAGE_SIZE = 4

ControlActionKind = Literal[
    "menu",
    "session_page",
    "model_page",
    "switch_session",
    "switch_model",
]


@dataclass(frozen=True)
class _ControlAction:
    kind: ControlActionKind
    key: IMBindingKey
    target: str = ""
    page: int = 0


@dataclass(frozen=True)
class _TokenEntry:
    action: _ControlAction
    expires_at: float


class _ControlTokenRegistry:
    def __init__(self, *, ttl_seconds: float = 600.0, max_items: int = 4096) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._entries: dict[str, _TokenEntry] = {}
        self._lock = threading.RLock()

    def issue(self, action: _ControlAction) -> str:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            while len(self._entries) >= self._max_items:
                oldest = min(self._entries, key=lambda item: self._entries[item].expires_at)
                self._entries.pop(oldest, None)
            token = secrets.token_urlsafe(12)
            self._entries[token] = _TokenEntry(
                action=action,
                expires_at=now + self._ttl_seconds,
            )
        return f"{_TOKEN_PREFIX}{token}"

    def consume(self, payload: str) -> _ControlAction | None:
        if not payload.startswith(_TOKEN_PREFIX):
            return None
        token = payload.removeprefix(_TOKEN_PREFIX)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.pop(token, None)
            self._prune(now)
        if entry is None or entry.expires_at <= now:
            return None
        return entry.action

    def _prune(self, now: float) -> None:
        expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
        for token in expired:
            self._entries.pop(token, None)


def menu_controls() -> OutboundControls:
    return OutboundControls(
        rows=(
            (
                OutboundAction(
                    id="agent-friend-menu",
                    label="☰ 菜单",
                    visited_label="菜单已打开",
                    payload=MENU_PAYLOAD,
                    style="default",
                ),
            ),
        )
    )


class IMControlService:
    def __init__(
        self,
        sessions: SessionManager,
        state_store: IMStateStore,
        coordinator: IMBindingCoordinator,
        model_service: SessionModelService,
    ) -> None:
        self._sessions = sessions
        self._state = state_store
        self._coordinator = coordinator
        self._models = model_service
        self._tokens = _ControlTokenRegistry()

    async def handle(self, provider: IMProvider, event: InboundInteraction) -> None:
        key = IMBindingKey(
            im_type=provider.type,
            provider_bind_id=provider.bind_id,
            chat_scope=event.chat_scope,
            chat_id=event.chat_id,
        )
        if event.operator_id != event.chat_id or event.chat_id != provider.bind_id:
            return

        if event.payload == MENU_PAYLOAD:
            await self._send_main_menu(provider, key)
            return

        action = self._tokens.consume(event.payload)
        if action is None or action.key != key:
            await self._send_text(
                provider,
                key,
                "这个菜单已经过期，请从最新回复重新打开。",
            )
            return

        if action.kind == "menu":
            await self._send_main_menu(provider, key)
        elif action.kind == "session_page":
            await self._send_session_page(provider, key, action.page)
        elif action.kind == "model_page":
            await self._send_model_page(provider, key, action.page)
        elif action.kind == "switch_session":
            await self._switch_session(provider, key, action.target)
        elif action.kind == "switch_model":
            await self._switch_model(provider, key, action.target)

    async def _send_main_menu(self, provider: IMProvider, key: IMBindingKey) -> None:
        current = self._bound_session(key)
        if current is None:
            await self._send_text(
                provider,
                key,
                "还没有选择会话，请先在 agent-friend 的“接入 IM”窗口完成选择。",
            )
            return
        title = self._display_title(current.session_id, current.initial_title)
        text = f"当前会话：{title}\n当前模型：{current.current_model}"
        controls = OutboundControls(
            rows=(
                (
                    self._token_button("switch-session", "切换会话", key, "session_page"),
                    self._token_button("switch-model", "切换模型", key, "model_page"),
                ),
            )
        )
        await self._send(provider, key, text, controls)

    async def _send_session_page(
        self,
        provider: IMProvider,
        key: IMBindingKey,
        page: int,
    ) -> None:
        sessions = self._sessions.list(archived=False)
        total_pages = max(1, (len(sessions) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(max(page, 0), total_pages - 1)
        start = page * _PAGE_SIZE
        current = self._state.get_binding(key)
        rows: list[tuple[OutboundAction, ...]] = []
        for summary in sessions[start : start + _PAGE_SIZE]:
            prefix = "当前 · " if current and current.session_id == summary.session_id else ""
            rows.append(
                (
                    self._token_button(
                        f"session-{summary.session_id[:12]}",
                        _truncate(f"{prefix}{summary.title}", 28),
                        key,
                        "switch_session",
                        target=summary.session_id,
                    ),
                )
            )
        nav: list[OutboundAction] = []
        if page > 0:
            nav.append(
                self._token_button("session-prev", "上一页", key, "session_page", page=page - 1)
            )
        if page + 1 < total_pages:
            nav.append(
                self._token_button("session-next", "下一页", key, "session_page", page=page + 1)
            )
        nav.append(self._token_button("session-back", "返回", key, "menu"))
        rows.append(tuple(nav))
        await self._send(
            provider,
            key,
            f"选择会话（第 {page + 1}/{total_pages} 页）",
            OutboundControls(rows=tuple(rows)),
        )

    async def _send_model_page(
        self,
        provider: IMProvider,
        key: IMBindingKey,
        page: int,
    ) -> None:
        models = _model_options()
        total_pages = max(1, (len(models) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(max(page, 0), total_pages - 1)
        start = page * _PAGE_SIZE
        current = self._bound_session(key)
        rows: list[tuple[OutboundAction, ...]] = []
        for model_id, label in models[start : start + _PAGE_SIZE]:
            prefix = "当前 · " if current and current.current_model == model_id else ""
            rows.append(
                (
                    self._token_button(
                        f"model-{secrets.token_hex(4)}",
                        _truncate(f"{prefix}{label}", 28),
                        key,
                        "switch_model",
                        target=model_id,
                    ),
                )
            )
        nav: list[OutboundAction] = []
        if page > 0:
            nav.append(self._token_button("model-prev", "上一页", key, "model_page", page=page - 1))
        if page + 1 < total_pages:
            nav.append(self._token_button("model-next", "下一页", key, "model_page", page=page + 1))
        nav.append(self._token_button("model-back", "返回", key, "menu"))
        rows.append(tuple(nav))
        await self._send(
            provider,
            key,
            f"选择模型（第 {page + 1}/{total_pages} 页）",
            OutboundControls(rows=tuple(rows)),
        )

    async def _switch_session(
        self,
        provider: IMProvider,
        key: IMBindingKey,
        session_id: str,
    ) -> None:
        try:
            async with self._coordinator.try_mutation(key):
                session = self._sessions.open(session_id)
                self._state.set_binding(key, session_id)
        except IMBindingBusyError:
            await self._send_text(provider, key, "请等待当前回复完成后再切换会话。")
            return
        except SessionNotFoundError:
            await self._send_text(provider, key, "目标会话已经不存在，请重新打开菜单选择。")
            return

        summary = self._summary(session_id)
        title = summary.title if summary is not None else session.initial_title
        updated_at = (
            summary.updated_at.astimezone().strftime("%Y-%m-%d %H:%M") if summary else "暂无"
        )
        preview = _last_assistant_preview(session)
        await self._send_text(
            provider,
            key,
            "已切换会话\n"
            f"标题：{title}\n"
            f"模型：{session.current_model}\n"
            f"最近活跃：{updated_at}\n"
            f"最近回复：{preview}",
        )

    async def _switch_model(
        self,
        provider: IMProvider,
        key: IMBindingKey,
        model_id: str,
    ) -> None:
        binding = self._state.get_binding(key)
        if binding is None:
            await self._send_text(provider, key, "还没有选择会话，请先在桌面端完成选择。")
            return
        try:
            async with self._coordinator.try_mutation(key):
                model = await asyncio.to_thread(self._models.switch, binding.session_id, model_id)
        except IMBindingBusyError:
            await self._send_text(provider, key, "请等待当前回复完成后再切换模型。")
            return
        except Exception as exc:
            error = map_exception(exc)
            await self._send_text(provider, key, f"模型没有切换：{error.message}")
            return
        await self._send_text(provider, key, f"模型已切换为 {model}，下一条回复开始生效。")

    def _token_button(
        self,
        action_id: str,
        label: str,
        key: IMBindingKey,
        kind: ControlActionKind,
        *,
        target: str = "",
        page: int = 0,
    ) -> OutboundAction:
        payload = self._tokens.issue(_ControlAction(kind=kind, key=key, target=target, page=page))
        return OutboundAction(
            id=action_id,
            label=label,
            visited_label="已选择",
            payload=payload,
            style="primary" if kind in ("switch_session", "switch_model") else "default",
        )

    def _bound_session(self, key: IMBindingKey) -> Session | None:
        binding = self._state.get_binding(key)
        if binding is None:
            return None
        try:
            return self._sessions.open(binding.session_id)
        except SessionNotFoundError:
            return None

    def _summary(self, session_id: str) -> SessionSummary | None:
        for summary in self._sessions.list(archived=False):
            if summary.session_id == session_id:
                return summary
        for summary in self._sessions.list(archived=True):
            if summary.session_id == session_id:
                return summary
        return None

    def _display_title(self, session_id: str, fallback: str) -> str:
        summary = self._summary(session_id)
        return summary.title if summary is not None else fallback

    async def _send_text(self, provider: IMProvider, key: IMBindingKey, text: str) -> None:
        await self._send(provider, key, text, None)

    @staticmethod
    async def _send(
        provider: IMProvider,
        key: IMBindingKey,
        text: str,
        controls: OutboundControls | None,
    ) -> None:
        await provider.send(
            OutboundContent(
                chat_id=key.chat_id,
                chat_scope=key.chat_scope,
                text=text,
                text_format="plain_text",
                controls=controls,
            )
        )


def _model_options() -> list[tuple[str, str]]:
    catalog = public_model_catalog()
    result: list[tuple[str, str]] = []
    for provider in catalog.get("providers", []):
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models", []):
            if isinstance(model, dict):
                model_id = str(model.get("id", ""))
                if model_id:
                    result.append((model_id, str(model.get("label", model_id))))
    return result


def _last_assistant_preview(session: object) -> str:
    messages = getattr(session, "messages", [])
    for message in reversed(messages):
        if getattr(message, "role", "") == "assistant":
            text = " ".join(str(getattr(message, "content", "")).split())
            return _truncate(text, 80) if text else "暂无"
    return "暂无"


def _truncate(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)] + "…"
