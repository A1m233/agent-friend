"""Agent run 的 cooperative cancellation 基础类型。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class CancelReason(StrEnum):
    CLIENT_DISCONNECTED = "client_disconnected"
    SUPERSEDED_BY_USER = "superseded_by_user"
    RUNTIME_STOPPING = "runtime_stopping"
    EXPLICIT_REQUEST = "explicit_request"
    CONSUMER_ABANDONED = "consumer_abandoned"
    DEDUPLICATED = "deduplicated"


@dataclass(frozen=True)
class Cancellation:
    reason: CancelReason
    requested_at: datetime


class RunCancelled(Exception):
    """运行在安全边界观察到取消信号。"""

    def __init__(self, cancellation: Cancellation) -> None:
        self.cancellation = cancellation
        super().__init__(f"run cancelled: {cancellation.reason}")


class CancellationToken:
    """线程安全、首个取消原因获胜的 cooperative token。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._cancellation: Cancellation | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancellation(self) -> Cancellation | None:
        with self._lock:
            return self._cancellation

    def cancel(self, reason: CancelReason) -> bool:
        with self._lock:
            if self._cancellation is not None:
                return False
            self._cancellation = Cancellation(reason=reason, requested_at=datetime.now(UTC))
            self._event.set()
            return True

    def raise_if_cancelled(self) -> None:
        cancellation = self.cancellation
        if cancellation is not None:
            raise RunCancelled(cancellation)

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)
