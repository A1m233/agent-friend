"""Platform-neutral orchestration for local social-platform login flows."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import ClassVar, Literal, Protocol, runtime_checkable

from .models import (
    SocialConnectorUnavailableError,
    SocialPlatform,
    SocialProviderError,
)

SocialAuthMode = Literal["required", "optional"]
SocialAuthChallengeKind = Literal["qr_image"]
SocialAuthState = Literal[
    "anonymous",
    "credential_present",
    "login_in_progress",
    "unavailable",
]


@dataclass(frozen=True)
class SocialAuthStatus:
    platform: SocialPlatform
    mode: SocialAuthMode
    state: SocialAuthState
    material_present: bool
    error: str | None = None


@dataclass(frozen=True)
class SocialAuthChallenge:
    """Ephemeral desktop-only material for one approved login request."""

    kind: SocialAuthChallengeKind
    media_type: str
    content: bytes


@dataclass(frozen=True)
class SocialAuthInteraction:
    """Request-scoped display channel owned by the desktop control plane."""

    publish_challenge: Callable[[SocialAuthChallenge], None]
    clear_challenge: Callable[[], None]


@runtime_checkable
class SocialAuthHandler(Protocol):
    platform: ClassVar[SocialPlatform]
    mode: ClassVar[SocialAuthMode]

    def status(self) -> SocialAuthStatus: ...

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus: ...

    def cancel_login(self) -> None: ...

    def close(self) -> None: ...


class SocialAuthManager:
    """Route explicit local login actions without exposing them as model tools."""

    def __init__(self, handlers: tuple[SocialAuthHandler, ...]) -> None:
        self._handlers: dict[SocialPlatform, SocialAuthHandler] = {}
        self._login_locks: dict[SocialPlatform, threading.Lock] = {}
        self._cancel_events: dict[SocialPlatform, threading.Event] = {}
        for handler in handlers:
            if handler.platform in self._handlers:
                raise ValueError(f"duplicate social auth handler: {handler.platform}")
            self._handlers[handler.platform] = handler
            self._login_locks[handler.platform] = threading.Lock()
            self._cancel_events[handler.platform] = threading.Event()

    def status(self, platform: SocialPlatform) -> SocialAuthStatus:
        handler = self._handler(platform)
        status = handler.status()
        if self._login_locks[platform].locked():
            return replace(status, state="login_in_progress")
        return status

    def login(
        self,
        platform: SocialPlatform,
        *,
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        handler = self._handler(platform)
        lock = self._login_locks[platform]
        if not lock.acquire(blocking=False):
            raise SocialProviderError(f"{platform} login is already running")
        cancel_event = self._cancel_events[platform]
        cancel_event.clear()
        try:
            return handler.login(
                cancelled=cancel_event.is_set,
                interaction=interaction,
            )
        finally:
            cancel_event.clear()
            lock.release()

    def cancel_login(self, platform: SocialPlatform) -> None:
        handler = self._handler(platform)
        self._cancel_events[platform].set()
        handler.cancel_login()

    def close(self) -> None:
        for platform, handler in self._handlers.items():
            self._cancel_events[platform].set()
            handler.close()

    def _handler(self, platform: SocialPlatform) -> SocialAuthHandler:
        handler = self._handlers.get(platform)
        if handler is None:
            raise SocialConnectorUnavailableError(
                f"login is unavailable for social platform {platform}"
            )
        return handler


__all__ = [
    "SocialAuthChallenge",
    "SocialAuthChallengeKind",
    "SocialAuthHandler",
    "SocialAuthInteraction",
    "SocialAuthManager",
    "SocialAuthMode",
    "SocialAuthState",
    "SocialAuthStatus",
]
