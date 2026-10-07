"""Confirmed provider receipts, separate from generated text and stream closure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class DeliveryFailure:
    outcome: Literal["failed", "unknown"]
    kind: str
    recoverable: bool


@dataclass(frozen=True)
class StreamDeliveryState:
    accepted_text: str = ""
    message_id: str | None = None
    terminal_accepted: bool = False
    failure: DeliveryFailure | None = None


@runtime_checkable
class TrackedStream(Protocol):
    """Optional capability; legacy streams need not invent receipt evidence."""

    def delivery_state(self) -> StreamDeliveryState: ...


@dataclass(frozen=True)
class ReplyDeliveryResult:
    outcome: Literal["delivered", "failed", "unknown"]
    accepted_chars: int
    terminal_accepted: bool
    recovery_attempted: bool = False
    duplicate_risk: bool = False
    error_kind: str | None = None
    message_ids: tuple[str, ...] = ()
