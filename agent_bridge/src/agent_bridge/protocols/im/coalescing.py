"""Short-window logical-input batching for QQ attachment events."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

from .coordinator import IMBindingCoordinator, IMBindingReservation
from .event import InboundEvent
from .state import IMBindingKey
from .voice import is_voice_attachment

logger = logging.getLogger(__name__)

__all__ = [
    "CoalescedInbound",
    "CoalescingDecision",
    "QQAttachmentTextCoalescer",
]

QQ_ATTACHMENT_TEXT_WINDOW_SECONDS = 3.0

DispatchReason = Literal["direct", "merged", "timeout", "barrier"]
PendingReason = Literal["started", "appended"]
TimeoutCallback = Callable[[IMBindingKey, str], Awaitable[None]]
SleepCallback = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class CoalescedInbound:
    """One event ready for the existing resolver/router path."""

    event: InboundEvent
    reason: DispatchReason
    source_message_ids: tuple[str, ...]
    waited_seconds: float | None = None


@dataclass(frozen=True)
class CoalescingDecision:
    """Immediate work produced by one inbound event."""

    dispatches: tuple[CoalescedInbound, ...] = ()
    held_message_id: str | None = None
    held_source_message_ids: tuple[str, ...] = ()
    held_attachment_count: int = 0
    held_reason: PendingReason | None = None
    dropped_message_ids: tuple[str, ...] = ()


@dataclass
class _PendingInbound:
    provider: object
    binding_key: IMBindingKey
    session_id: str
    event: InboundEvent
    source_message_ids: tuple[str, ...]
    token: str
    started_at: float
    last_updated_at: float
    reservation: IMBindingReservation
    on_timeout: TimeoutCallback
    timeout_task: asyncio.Task[None] | None = None


class QQAttachmentTextCoalescer:
    """Batch QQ attachment fragments into one logical user input.

    Public state mutations are synchronous and never await. ``IMRuntime`` invokes
    them on its main asyncio loop before acquiring the long-lived Agent turn lock,
    so attachments can keep joining a batch while an earlier reply is running.
    """

    def __init__(
        self,
        coordinator: IMBindingCoordinator,
        *,
        window_seconds: float = QQ_ATTACHMENT_TEXT_WINDOW_SECONDS,
        max_batch_seconds: float | None = None,
        sleep: SleepCallback = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        if window_seconds < 0:
            raise ValueError("coalescing window must be non-negative")
        if max_batch_seconds is not None and max_batch_seconds < 0:
            raise ValueError("coalescing hard limit must be non-negative")
        self._coordinator = coordinator
        self._window_seconds = window_seconds
        self._max_batch_seconds = max_batch_seconds
        self._sleep = sleep
        self._clock = clock
        self._pending: dict[IMBindingKey, _PendingInbound] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def max_batch_seconds(self) -> float | None:
        return self._max_batch_seconds

    def offer(
        self,
        *,
        provider: object,
        binding_key: IMBindingKey,
        session_id: str,
        event: InboundEvent,
        on_timeout: TimeoutCallback,
    ) -> CoalescingDecision:
        """Consume, hold, or release ``event`` according to the QQ state machine."""

        if binding_key.im_type != "qq" or event.chat_scope != "c2c":
            return CoalescingDecision(dispatches=(_dispatch(event, "direct"),))

        pending = self._pending.get(binding_key)
        dropped: tuple[str, ...] = ()
        if pending is not None and not self._same_owner(pending, provider, session_id):
            removed = self._remove_pending(binding_key, pending.token)
            if removed is not None:
                dropped = removed.source_message_ids
            pending = None

        now = self._clock()
        if pending is not None and self._is_expired(pending, now):
            removed = self._remove_pending(binding_key, pending.token)
            pending = None
            expired_dispatch = (
                self._dispatch_pending(removed, "timeout", now=now) if removed is not None else None
            )
            if _is_attachment_only_candidate(event):
                started = self._start_pending(
                    provider=provider,
                    binding_key=binding_key,
                    session_id=session_id,
                    event=event,
                    on_timeout=on_timeout,
                    now=now,
                )
                return CoalescingDecision(
                    dispatches=((expired_dispatch,) if expired_dispatch is not None else ()),
                    held_message_id=event.message_id,
                    held_source_message_ids=started.source_message_ids,
                    held_attachment_count=len(_all_attachments(started.event)),
                    held_reason="started",
                    dropped_message_ids=dropped,
                )
            dispatches = (expired_dispatch,) if expired_dispatch is not None else ()
            return CoalescingDecision(
                dispatches=(*dispatches, _dispatch(event, "direct")),
                dropped_message_ids=dropped,
            )

        if pending is None:
            if _is_attachment_only_candidate(event):
                started = self._start_pending(
                    provider=provider,
                    binding_key=binding_key,
                    session_id=session_id,
                    event=event,
                    on_timeout=on_timeout,
                    now=now,
                )
                return CoalescingDecision(
                    held_message_id=event.message_id,
                    held_source_message_ids=started.source_message_ids,
                    held_attachment_count=len(_all_attachments(started.event)),
                    held_reason="started",
                    dropped_message_ids=dropped,
                )
            return CoalescingDecision(
                dispatches=(_dispatch(event, "direct"),),
                dropped_message_ids=dropped,
            )

        if _is_pure_text(event):
            removed = self._remove_pending(binding_key, pending.token)
            if removed is None:  # pragma: no cover - synchronous state mutation invariant.
                return CoalescingDecision(dispatches=(_dispatch(event, "direct"),))
            merged = _merge_attachment_and_text(removed.event, event)
            return CoalescingDecision(
                dispatches=(
                    CoalescedInbound(
                        event=merged,
                        reason="merged",
                        source_message_ids=(*removed.source_message_ids, *_message_ids(event)),
                        waited_seconds=max(0.0, now - removed.started_at),
                    ),
                )
            )

        if _is_attachment_only_candidate(event):
            appended = self._append_pending(pending, event, now=now)
            return CoalescingDecision(
                held_message_id=event.message_id,
                held_source_message_ids=appended.source_message_ids,
                held_attachment_count=len(_all_attachments(appended.event)),
                held_reason="appended",
            )

        removed = self._remove_pending(binding_key, pending.token)
        if removed is None:  # pragma: no cover - synchronous state mutation invariant.
            return CoalescingDecision(dispatches=(_dispatch(event, "direct"),))
        return CoalescingDecision(
            dispatches=(
                _dispatch(
                    removed.event,
                    "barrier",
                    waited_seconds=max(0.0, now - removed.started_at),
                    source_message_ids=removed.source_message_ids,
                ),
                _dispatch(event, "direct"),
            )
        )

    def expire(
        self,
        *,
        provider: object,
        binding_key: IMBindingKey,
        session_id: str,
        token: str,
    ) -> CoalescingDecision:
        """Release the current pending event if ``token`` still owns it."""

        pending = self._pending.get(binding_key)
        if pending is None or pending.token != token:
            return CoalescingDecision()
        if not self._same_owner(pending, provider, session_id):
            removed = self._remove_pending(binding_key, token)
            return CoalescingDecision(
                dropped_message_ids=removed.source_message_ids if removed is not None else ()
            )
        removed = self._remove_pending(binding_key, token)
        if removed is None:  # pragma: no cover - token was checked above.
            return CoalescingDecision()
        return CoalescingDecision(
            dispatches=(self._dispatch_pending(removed, "timeout", now=self._clock()),)
        )

    def cancel_provider(self, provider: object) -> tuple[str, ...]:
        """Cancel every pending event owned by ``provider`` without dispatching it."""

        removed_ids: list[str] = []
        for key, pending in tuple(self._pending.items()):
            if pending.provider is not provider:
                continue
            removed = self._remove_pending(key, pending.token)
            if removed is not None:
                removed_ids.extend(removed.source_message_ids)
        return tuple(removed_ids)

    async def cancel_all(self) -> tuple[str, ...]:
        """Cancel all pending timers and wait for their cancellation to settle."""

        removed_ids: list[str] = []
        tasks: list[asyncio.Task[None]] = []
        for key, pending in tuple(self._pending.items()):
            if pending.timeout_task is not None:
                tasks.append(pending.timeout_task)
            removed = self._remove_pending(key, pending.token)
            if removed is not None:
                removed_ids.extend(removed.source_message_ids)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return tuple(removed_ids)

    def _start_pending(
        self,
        *,
        provider: object,
        binding_key: IMBindingKey,
        session_id: str,
        event: InboundEvent,
        on_timeout: TimeoutCallback,
        now: float,
    ) -> _PendingInbound:
        token = uuid4().hex
        reservation = self._coordinator.reserve_pending_turn(binding_key)
        pending = _PendingInbound(
            provider=provider,
            binding_key=binding_key,
            session_id=session_id,
            event=event,
            source_message_ids=_message_ids(event),
            token=token,
            started_at=now,
            last_updated_at=now,
            reservation=reservation,
            on_timeout=on_timeout,
        )
        self._pending[binding_key] = pending
        self._schedule_timeout(pending, now=now)
        return pending

    def _append_pending(
        self,
        pending: _PendingInbound,
        event: InboundEvent,
        *,
        now: float,
    ) -> _PendingInbound:
        timeout_task = pending.timeout_task
        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()
        pending.event = _merge_attachment_events(pending.event, event)
        pending.source_message_ids = (*pending.source_message_ids, *_message_ids(event))
        pending.last_updated_at = now
        self._schedule_timeout(pending, now=now)
        return pending

    def _schedule_timeout(self, pending: _PendingInbound, *, now: float) -> None:
        delay = self._window_seconds
        if self._max_batch_seconds is not None:
            hard_remaining = max(
                0.0,
                self._max_batch_seconds - max(0.0, now - pending.started_at),
            )
            delay = min(delay, hard_remaining)

        async def wait_then_expire() -> None:
            await self._sleep(delay)
            await pending.on_timeout(pending.binding_key, pending.token)

        task = asyncio.create_task(wait_then_expire())
        task.add_done_callback(self._task_done)
        self._tasks.add(task)
        pending.timeout_task = task

    def _remove_pending(self, binding_key: IMBindingKey, token: str) -> _PendingInbound | None:
        pending = self._pending.get(binding_key)
        if pending is None or pending.token != token:
            return None
        self._pending.pop(binding_key, None)
        current_task = asyncio.current_task()
        timeout_task = pending.timeout_task
        if (
            timeout_task is not None
            and timeout_task is not current_task
            and not timeout_task.done()
        ):
            timeout_task.cancel()
        released = self._coordinator.release_pending_turn(pending.reservation)
        if not released:  # pragma: no cover - defensive invariant logging.
            logger.warning(
                "IM coalescing reservation already released message_id=%s",
                pending.event.message_id,
            )
        return pending

    @staticmethod
    def _same_owner(pending: _PendingInbound, provider: object, session_id: str) -> bool:
        return pending.provider is provider and pending.session_id == session_id

    def _is_expired(self, pending: _PendingInbound, now: float) -> bool:
        idle_elapsed = max(0.0, now - pending.last_updated_at)
        total_elapsed = max(0.0, now - pending.started_at)
        hard_expired = (
            self._max_batch_seconds is not None and total_elapsed >= self._max_batch_seconds
        )
        return idle_elapsed >= self._window_seconds or hard_expired

    @staticmethod
    def _dispatch_pending(
        pending: _PendingInbound,
        reason: DispatchReason,
        *,
        now: float,
    ) -> CoalescedInbound:
        return _dispatch(
            pending.event,
            reason,
            waited_seconds=max(0.0, now - pending.started_at),
            source_message_ids=pending.source_message_ids,
        )

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "IM coalescing timeout task failed",
                exc_info=(type(exc), exc, exc.__traceback__),
            )


def _dispatch(
    event: InboundEvent,
    reason: DispatchReason,
    *,
    waited_seconds: float | None = None,
    source_message_ids: tuple[str, ...] | None = None,
) -> CoalescedInbound:
    return CoalescedInbound(
        event=event,
        reason=reason,
        source_message_ids=(
            source_message_ids if source_message_ids is not None else _message_ids(event)
        ),
        waited_seconds=waited_seconds,
    )


def _merge_attachment_events(
    earlier_event: InboundEvent,
    later_event: InboundEvent,
) -> InboundEvent:
    """Use the last QQ event as reply anchor while retaining all attachments."""

    return replace(
        later_event,
        attachments=[*earlier_event.attachments, *later_event.attachments],
        msg_elements=[*earlier_event.msg_elements, *later_event.msg_elements],
    )


def _merge_attachment_and_text(
    attachment_event: InboundEvent,
    text_event: InboundEvent,
) -> InboundEvent:
    return replace(
        text_event,
        attachments=[*attachment_event.attachments, *text_event.attachments],
        msg_elements=[*attachment_event.msg_elements, *text_event.msg_elements],
    )


def _is_pure_text(event: InboundEvent) -> bool:
    return bool((event.content or "").strip()) and not _all_attachments(event)


def _is_attachment_only_candidate(event: InboundEvent) -> bool:
    if (event.content or "").strip():
        return False
    attachments = _all_attachments(event)
    return bool(attachments) and all(_attachment_can_wait(item) for item in attachments)


def _attachment_can_wait(attachment: object) -> bool:
    return not is_voice_attachment(attachment)


def _all_attachments(event: InboundEvent) -> list[object]:
    attachments: list[object] = list(event.attachments)
    for element in event.msg_elements:
        attachments.extend(getattr(element, "attachments", []) or [])
    return attachments


def _message_ids(event: InboundEvent) -> tuple[str, ...]:
    return (event.message_id,) if event.message_id else ()
