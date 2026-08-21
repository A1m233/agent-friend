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

from ..latency import monotonic_ms
from ..system_prompt.sections import PromptChannel
from ..system_prompt.surfaces import InteractionSurfaceId
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


class TurnStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class ConsumerAbandonPolicy(StrEnum):
    """Primary event consumer disappears before the run reaches terminal."""

    CANCEL = "cancel"
    DETACH = "detach"


class InteractiveBusyPolicy(StrEnum):
    """Admission behavior when the same session already has interactive work."""

    QUEUE = "queue"
    REJECT_IF_INTERACTIVE_BUSY = "reject_if_interactive_busy"


class RunCancelResult(StrEnum):
    ACCEPTED = "accepted"
    ALREADY_REQUESTED = "already_requested"
    ALREADY_TERMINAL = "already_terminal"
    NOT_FOUND = "not_found"
    SESSION_MISMATCH = "session_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"


@dataclass(frozen=True)
class RunTerminal:
    outcome: RunOutcome
    turn_status: TurnStatus | None = None
    reason_code: str = ""
    total_tool_calls: int = 0
    tool_call_items: int = 0
    model_calls: int = 0
    model_continuations: int = 0
    budget_dimension: str | None = None
    error: BaseException | None = None
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))


ConversationFactory = Callable[[], "Conversation"]
RunErrorCallback = Callable[["Conversation", BaseException, "RunRequest"], None]
ConversationRunner = Callable[
    ["Conversation", "RunRequest", CancellationToken], Iterator["ConversationEvent"]
]


@dataclass(frozen=True)
class RunExecutionResult:
    turn_status: TurnStatus
    reason_code: str
    total_tool_calls: int = 0
    tool_call_items: int = 0
    model_calls: int = 0
    model_continuations: int = 0
    budget_dimension: str | None = None


@dataclass(frozen=True)
class RunRequest:
    event: AgentEvent
    run_id: str = field(default_factory=lambda: uuid4().hex)
    priority: RunPriority = RunPriority.INTERACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    dedupe_key: str | None = None
    channel: PromptChannel | None = None
    surface: InteractionSurfaceId | None = None
    conversation_factory: ConversationFactory | None = None
    conversation_runner: ConversationRunner | None = None
    error_callback: RunErrorCallback | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    consumer_abandon_policy: ConsumerAbandonPolicy = ConsumerAbandonPolicy.CANCEL
    interactive_busy_policy: InteractiveBusyPolicy = InteractiveBusyPolicy.QUEUE
    control_scope: str | None = None
    latency_started_ms: int = field(default_factory=monotonic_ms)
    scheduler_submitted_ms: int = field(default_factory=monotonic_ms)

    @property
    def session_id(self) -> str:
        return self.event.session_id


_STREAM_END = object()


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    session_id: str
    status: RunStatus
    outcome: RunOutcome | None
    reason_code: str
    channel: str | None
    origin: str | None
    control_scope: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    version: int
    turn_status: TurnStatus | None = None
    total_tool_calls: int = 0
    tool_call_items: int = 0
    model_calls: int = 0
    model_continuations: int = 0
    budget_dimension: str | None = None


RunChangeCallback = Callable[[RunSnapshot], None]


class RunHandle:
    """一次运行的线程安全句柄；terminal transition 只允许成功一次。"""

    def __init__(
        self,
        request: RunRequest,
        *,
        event_queue_size: int = 128,
        primary_consumer: bool = True,
        on_change: RunChangeCallback | None = None,
    ) -> None:
        self.request = request
        self.token = CancellationToken()
        self._status = RunStatus.QUEUED
        self._terminal: RunTerminal | None = None
        self._started_at: datetime | None = None
        self._version = 1
        self._lock = threading.Lock()
        self._terminal_event = threading.Event()
        self._events: queue.Queue[object] = queue.Queue(maxsize=event_queue_size)
        self._primary_consumer = primary_consumer
        self._consumer_attached = primary_consumer
        self._on_change = on_change

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

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def publish_initial(self) -> None:
        self._publish(self.snapshot())

    def mark_running(self) -> bool:
        with self._lock:
            if self._status is not RunStatus.QUEUED:
                return False
            self._status = RunStatus.RUNNING
            self._started_at = datetime.now(UTC)
            self._version += 1
            snapshot = self._snapshot_locked()
        self._publish(snapshot)
        return True

    def cancel(self, reason: CancelReason = CancelReason.EXPLICIT_REQUEST) -> bool:
        return self.token.cancel(reason)

    def emit(self, event: object, *, timeout: float = 0.1) -> None:
        """向 primary consumer 发事件；取消时停止等待满队列。"""
        if not self._consumer_is_attached():
            return
        while True:
            self.token.raise_if_cancelled()
            if not self._consumer_is_attached():
                return
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
        turn_status: TurnStatus | None = None,
        total_tool_calls: int = 0,
        tool_call_items: int = 0,
        model_calls: int = 0,
        model_continuations: int = 0,
        budget_dimension: str | None = None,
    ) -> bool:
        with self._lock:
            if self._status is RunStatus.TERMINAL:
                return False
            self._status = RunStatus.TERMINAL
            self._terminal = RunTerminal(
                outcome=outcome,
                turn_status=turn_status,
                reason_code=reason_code,
                total_tool_calls=total_tool_calls,
                tool_call_items=tool_call_items,
                model_calls=model_calls,
                model_continuations=model_continuations,
                budget_dimension=budget_dimension,
                error=error,
            )
            self._version += 1
            self._terminal_event.set()
            snapshot = self._snapshot_locked()
            consumer_attached = self._consumer_attached
        self._publish(snapshot)
        if not consumer_attached:
            return True
        # terminal sentinel 不能被满队列永久卡住；consumer 会先排空已有事件。
        while True:
            try:
                self._events.put(_STREAM_END, timeout=0.1)
                break
            except queue.Full:
                if not self._consumer_is_attached():
                    return True
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
                self.abandon_consumer()

    def abandon_consumer(self) -> None:
        """Apply the request's explicit consumer-abandon policy once."""
        should_cancel = False
        with self._lock:
            if not self._consumer_attached or self._status is RunStatus.TERMINAL:
                return
            self._consumer_attached = False
            should_cancel = self.request.consumer_abandon_policy is ConsumerAbandonPolicy.CANCEL
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break
        if should_cancel:
            self.cancel(CancelReason.CONSUMER_ABANDONED)

    def wait(self, timeout: float | None = None) -> RunTerminal | None:
        if not self._terminal_event.wait(timeout):
            return None
        return self.terminal

    def _consumer_is_attached(self) -> bool:
        with self._lock:
            return self._consumer_attached

    def _snapshot_locked(self) -> RunSnapshot:
        terminal = self._terminal
        origin = self.request.metadata.get("protocol")
        if origin is None and self.request.event.__class__.__name__ == "SystemTriggerEvent":
            origin = "system_trigger"
        return RunSnapshot(
            run_id=self.request.run_id,
            session_id=self.request.session_id,
            status=self._status,
            outcome=terminal.outcome if terminal is not None else None,
            reason_code=terminal.reason_code if terminal is not None else "",
            channel=str(self.request.channel) if self.request.channel is not None else None,
            origin=origin if isinstance(origin, str) else None,
            control_scope=self.request.control_scope,
            created_at=self.request.created_at,
            started_at=self._started_at,
            finished_at=terminal.finished_at if terminal is not None else None,
            version=self._version,
            turn_status=terminal.turn_status if terminal is not None else None,
            total_tool_calls=terminal.total_tool_calls if terminal is not None else 0,
            tool_call_items=terminal.tool_call_items if terminal is not None else 0,
            model_calls=terminal.model_calls if terminal is not None else 0,
            model_continuations=terminal.model_continuations if terminal is not None else 0,
            budget_dimension=terminal.budget_dimension if terminal is not None else None,
        )

    def _publish(self, snapshot: RunSnapshot) -> None:
        if self._on_change is not None:
            self._on_change(snapshot)
