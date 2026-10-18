"""Bounded, process-local, delivery-gated approval actions."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..provider import IMProvider
from ..state import IMSessionBinding

TOKEN_PREFIX = "af:approval:v1:"
ActionKind = Literal["page", "enable", "confirm", "cancel", "manual", "allow", "deny"]


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    binding: IMSessionBinding
    provider: IMProvider
    group: str
    mode_version: int = 0
    run_id: str = ""
    request_id: str = ""
    request_kind: str = ""
    request_version: int = 0


@dataclass
class _Entry:
    action: Action
    expires_at: float
    delivered: bool = False


class ApprovalTokens:
    def __init__(self, *, ttl: float = 300.0, capacity: int = 4096) -> None:
        self._ttl = ttl
        self._capacity = capacity
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()

    def issue(self, action: Action) -> str:
        with self._lock:
            self._prune()
            while len(self._entries) >= self._capacity:
                self._entries.pop(next(iter(self._entries)))
            token = TOKEN_PREFIX + secrets.token_urlsafe(18)
            self._entries[token] = _Entry(action, time.monotonic() + self._ttl)
            return token

    def delivered(self, group: str) -> None:
        with self._lock:
            self._prune()
            for entry in self._entries.values():
                if entry.action.group == group:
                    entry.delivered = True

    def resolve(self, token: str) -> Action | None:
        with self._lock:
            self._prune()
            entry = self._entries.get(token)
            return entry.action if entry is not None and entry.delivered else None

    def consume(self, token: str) -> Action | None:
        with self._lock:
            action = self.resolve(token)
            if action is not None:
                self.invalidate(lambda item: item.group == action.group)
            return action

    def invalidate(self, predicate: Callable[[Action], bool]) -> None:
        with self._lock:
            for token in list(self._entries):
                if predicate(self._entries[token].action):
                    self._entries.pop(token, None)

    def _prune(self) -> None:
        now = time.monotonic()
        for token in list(self._entries):
            if self._entries[token].expires_at <= now:
                del self._entries[token]
