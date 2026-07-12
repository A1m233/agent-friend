"""AgentRuntime 的运行身份、状态与 primary event stream。"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..system_prompt.sections import PromptChannel
from .cancellation import CancellationToken, CancelReason
from .inbox import AgentEvent

if TYPE_CHECKING:
    from ..conversation import Conversation
    from ..conversation_events import ConversationEvent


class RunPriority(IntEnum):
    INTERACTIVE = 0
    PROACTIVE = 10
    BACKGROUND = 20


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    TERMINAL = "terminal"


class RunOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RunTerminal:
    outcome: RunOutcome
    reason_code: str = ""
    error: BaseException | None = None
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))


ConversationFactory = Callable[[], "Conversation"]
RunErrorCallback = Callable[["Conversation", BaseException, "RunRequest"], None]
ConversationRunner = Callable[
    ["Conversation", "RunRequest", CancellationToken], Iterator["ConversationEvent"]
]


@dataclass(frozen=True)
class RunRequest:
    event: AgentEvent
    run_id: str = field(default_factory=lambda: uuid4().hex)
    priority: RunPriority = RunPriority.INTERACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    dedupe_key: str | None = None
    channel: PromptChannel | None = None
    conversation_factory: ConversationFactory | None = None
    conversation_runner: ConversationRunner | None = None
    error_callback: RunErrorCallback | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return self.event.session_id


_STREAM_END = object()


class RunHandle:
    """一次运行的线程安全句柄；terminal transition 只允许成功一次。"""

    def __init__(
        self,
        request: RunRequest,
        *,
        event_queue_size: int = 128,
        primary_consumer: bool = True,
    ) -> None:
        self.request = request
        self.token = CancellationToken()
        self._status = RunStatus.QUEUED
        self._terminal: RunTerminal | None = None
        self._lock = threading.Lock()
        self._terminal_event = threading.Event()
        self._events: queue.Queue[object] = queue.Queue(maxsize=event_queue_size)
        self._primary_consumer = primary_consumer

    @property
    def run_id(self) -> str:
        return self.request.run_id

    @property
    def status(self) -> RunStatus:
        with self._lock:
            return self._status

    @property
    def terminal(self) -> RunTerminal | None:
        with self._lock:
            return self._terminal

    def mark_running(self) -> bool:
        with self._lock:
            if self._status is not RunStatus.QUEUED:
                return False
            self._status = RunStatus.RUNNING
            return True

    def cancel(self, reason: CancelReason = CancelReason.EXPLICIT_REQUEST) -> bool:
        return self.token.cancel(reason)

    def emit(self, event: object, *, timeout: float = 0.1) -> None:
        """向 primary consumer 发事件；取消时停止等待满队列。"""
        if not self._primary_consumer:
            return
        while True:
            self.token.raise_if_cancelled()
            try:
                self._events.put(event, timeout=timeout)
                return
            except queue.Full:
                continue

    def finish_once(
        self,
        outcome: RunOutcome,
        *,
        reason_code: str = "",
        error: BaseException | None = None,
    ) -> bool:
        with self._lock:
            if self._status is RunStatus.TERMINAL:
                return False
            self._status = RunStatus.TERMINAL
            self._terminal = RunTerminal(
                outcome=outcome,
                reason_code=reason_code,
                error=error,
            )
            self._terminal_event.set()
        if not self._primary_consumer:
            return True
        # terminal sentinel 不能被满队列永久卡住；consumer 会先排空已有事件。
        while True:
            try:
                self._events.put(_STREAM_END, timeout=0.1)
                break
            except queue.Full:
                if self.token.is_cancelled:
                    with suppress(queue.Empty):
                        self._events.get_nowait()
        return True

    def events(self) -> Iterator[object]:
        try:
            while True:
                event = self._events.get()
                if event is _STREAM_END:
                    return
                yield event
        finally:
            if self.status is not RunStatus.TERMINAL:
                self.cancel(CancelReason.CONSUMER_ABANDONED)

    def wait(self, timeout: float | None = None) -> RunTerminal | None:
        if not self._terminal_event.wait(timeout):
            return None
        return self.terminal
