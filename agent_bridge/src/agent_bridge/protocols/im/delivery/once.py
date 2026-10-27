"""Shared at-most-once send sequence; callers retain their own durable ledgers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..provider import OutboundSendResult


@dataclass(frozen=True)
class DeliveryAttempt:
    claim: Callable[[], bool]
    settle: Callable[[OutboundSendResult], None]


def deliver_once(
    attempts: Sequence[DeliveryAttempt], send: Callable[[], OutboundSendResult | None]
) -> bool:
    """Claim every participating record before one (possibly batched) send.

    Claim/settlement failures propagate. Already claimed rows remain uncertain,
    so re-entry cannot replay the external effect after a storage failure.
    """
    started = [attempt for attempt in attempts if attempt.claim()]
    if not started:
        return False
    try:
        result = send() or OutboundSendResult("unknown", error_kind="missing_receipt")
    except Exception:
        result = OutboundSendResult("unknown", error_kind="send_exception")
    for attempt in started:
        attempt.settle(result)
    return True
