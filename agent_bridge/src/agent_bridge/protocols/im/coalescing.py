"""Short-window coalescing for QQ attachment messages followed by text."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
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

DispatchReason = Literal["direct", "merged", "timeout", "next_attachment", "barrier"]
TimeoutCallback = Callable[[IMBindingKey, str], Awaitable[None]]
SleepCallback = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]

_SUPPORTED_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"}
)
_SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt"})
_EXPLICITLY_UNSUPPORTED_EXTENSIONS = frozenset(
    {
        ".gif",
        ".mp3",
        ".m4a",
        ".ogg",
        ".opus",
        ".wav",
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)
_GENERIC_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})


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
    dropped_message_ids: tuple[str, ...] = ()


@dataclass
class _PendingInbound:
    provider: object
    binding_key: IMBindingKey
    session_id: str
    event: InboundEvent
    token: str
    started_at: float
    reservation: IMBindingReservation
    timeout_task: asyncio.Task[None]


class QQAttachmentTextCoalescer:
    """Hold one QQ attachment-only event per binding for a short text window.

    State mutations are expected to run while the caller holds the matching
    :class:`IMBindingCoordinator` turn lock. Timeout tasks only signal the
    runtime; the runtime reacquires that same lock before calling :meth:`expire`.
    """

    def __init__(
        self,
        coordinator: IMBindingCoordinator,
        *,
        window_seconds: float = QQ_ATTACHMENT_TEXT_WINDOW_SECONDS,
        sleep: SleepCallback = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        if window_seconds < 0:
            raise ValueError("coalescing window must be non-negative")
        self._coordinator = coordinator
        self._window_seconds = window_seconds
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
                dropped = _message_ids(removed.event)
            pending = None

        if pending is None:
            if _is_attachment_only_candidate(event):
                self._start_pending(
                    provider=provider,
                    binding_key=binding_key,
                    session_id=session_id,
                    event=event,
                    on_timeout=on_timeout,
                )
                return CoalescingDecision(
                    held_message_id=event.message_id,
                    dropped_message_ids=dropped,
                )
            return CoalescingDecision(
                dispatches=(_dispatch(event, "direct"),),
                dropped_message_ids=dropped,
            )

        if _is_pure_text(event):
            removed = self._remove_pending(binding_key, pending.token)
            if removed is None:  # pragma: no cover - guarded by the binding turn lock.
                return CoalescingDecision(dispatches=(_dispatch(event, "direct"),))
            merged = _merge_attachment_and_text(removed.event, event)
            return CoalescingDecision(
                dispatches=(
                    CoalescedInbound(
                        event=merged,
                        reason="merged",
                        source_message_ids=(*_message_ids(removed.event), *_message_ids(event)),
                        waited_seconds=max(0.0, self._clock() - removed.started_at),
                    ),
                )
            )

        if _is_attachment_only_candidate(event):
            removed = self._remove_pending(binding_key, pending.token)
            if removed is None:  # pragma: no cover - guarded by the binding turn lock.
                return CoalescingDecision(dispatches=(_dispatch(event, "direct"),))
            self._start_pending(
                provider=provider,
                binding_key=binding_key,
                session_id=session_id,
                event=event,
                on_timeout=on_timeout,
            )
            return CoalescingDecision(
                dispatches=(
                    _dispatch(
                        removed.event,
                        "next_attachment",
                        waited_seconds=max(0.0, self._clock() - removed.started_at),
                    ),
                ),
                held_message_id=event.message_id,
            )

        removed = self._remove_pending(binding_key, pending.token)
        if removed is None:  # pragma: no cover - guarded by the binding turn lock.
            return CoalescingDecision(dispatches=(_dispatch(event, "direct"),))
        return CoalescingDecision(
            dispatches=(
                _dispatch(
                    removed.event,
                    "barrier",
                    waited_seconds=max(0.0, self._clock() - removed.started_at),
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
                dropped_message_ids=_message_ids(removed.event) if removed is not None else ()
            )
        removed = self._remove_pending(binding_key, token)
        if removed is None:  # pragma: no cover - token was checked above.
            return CoalescingDecision()
        return CoalescingDecision(
            dispatches=(
                _dispatch(
                    removed.event,
                    "timeout",
                    waited_seconds=max(0.0, self._clock() - removed.started_at),
                ),
            )
        )

    def cancel_provider(self, provider: object) -> tuple[str, ...]:
        """Cancel every pending event owned by ``provider`` without dispatching it."""

        removed_ids: list[str] = []
        for key, pending in tuple(self._pending.items()):
            if pending.provider is not provider:
                continue
            removed = self._remove_pending(key, pending.token)
            if removed is not None:
                removed_ids.extend(_message_ids(removed.event))
        return tuple(removed_ids)

    async def cancel_all(self) -> tuple[str, ...]:
        """Cancel all pending timers and wait for their cancellation to settle."""

        removed_ids: list[str] = []
        tasks: list[asyncio.Task[None]] = []
        for key, pending in tuple(self._pending.items()):
            tasks.append(pending.timeout_task)
            removed = self._remove_pending(key, pending.token)
            if removed is not None:
                removed_ids.extend(_message_ids(removed.event))
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
    ) -> None:
        token = uuid4().hex
        reservation = self._coordinator.reserve_pending_turn(binding_key)

        async def wait_then_expire() -> None:
            await self._sleep(self._window_seconds)
            await on_timeout(binding_key, token)

        task = asyncio.create_task(wait_then_expire())
        task.add_done_callback(self._task_done)
        self._tasks.add(task)
        self._pending[binding_key] = _PendingInbound(
            provider=provider,
            binding_key=binding_key,
            session_id=session_id,
            event=event,
            token=token,
            started_at=self._clock(),
            reservation=reservation,
            timeout_task=task,
        )

    def _remove_pending(self, binding_key: IMBindingKey, token: str) -> _PendingInbound | None:
        pending = self._pending.get(binding_key)
        if pending is None or pending.token != token:
            return None
        self._pending.pop(binding_key, None)
        current_task = asyncio.current_task()
        if pending.timeout_task is not current_task and not pending.timeout_task.done():
            pending.timeout_task.cancel()
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
) -> CoalescedInbound:
    return CoalescedInbound(
        event=event,
        reason=reason,
        source_message_ids=_message_ids(event),
        waited_seconds=waited_seconds,
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
    if is_voice_attachment(attachment):
        return False
    media_type = str(getattr(attachment, "content_type", "") or "")
    media_type = media_type.split(";", 1)[0].strip().lower()
    filename = str(getattr(attachment, "filename", "") or "").strip()
    suffix = Path(filename).suffix.lower()

    if media_type == "image/gif" or suffix == ".gif":
        return False
    if media_type.startswith(("audio/", "video/")):
        return False
    if suffix in _EXPLICITLY_UNSUPPORTED_EXTENSIONS:
        return False
    if media_type in _SUPPORTED_MEDIA_TYPES or suffix in _SUPPORTED_EXTENSIONS:
        return True
    return media_type in _GENERIC_MEDIA_TYPES


def _all_attachments(event: InboundEvent) -> list[object]:
    attachments: list[object] = list(event.attachments)
    for element in event.msg_elements:
        attachments.extend(getattr(element, "attachments", []) or [])
    return attachments


def _message_ids(event: InboundEvent) -> tuple[str, ...]:
    return (event.message_id,) if event.message_id else ()
