"""Cooperative cancellation shared by the tool executor and long-running tools."""

from __future__ import annotations

import threading
from collections.abc import Callable
from uuid import uuid4

TOOL_INVOCATION_CONTROL_ARG = "__agent_friend_tool_invocation_control"


class ToolInvocationCancelled(Exception):
    """Internal cooperative stop, never a failed evidence source fallback."""


class ToolInvocationControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._reason = "cancelled"
        self._callbacks: dict[str, Callable[[str], None]] = {}

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise ToolInvocationCancelled(self._reason)

    def add_cancel_callback(self, callback: Callable[[str], None]) -> str:
        token = uuid4().hex
        with self._lock:
            if not self._cancelled.is_set():
                self._callbacks[token] = callback
                return token
            reason = self._reason
        callback(reason)
        return token

    def remove_cancel_callback(self, token: str) -> None:
        with self._lock:
            self._callbacks.pop(token, None)

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self._cancelled.is_set():
                return
            self._reason = reason
            self._cancelled.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback(reason)
            except Exception:
                continue
