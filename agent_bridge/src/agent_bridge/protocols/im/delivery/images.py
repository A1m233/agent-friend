"""Ordered, bounded rich-media delivery; a failed send never reruns generation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from ..content import OutboundContent
from ..provider import OutboundSendResult
from ..streaming import IMStreamSession
from .models import TrackedStream
from .recovery import recover_reply, send_observed

logger = logging.getLogger(__name__)


async def deliver_images(
    prefix: OutboundContent,
    parts: list[OutboundContent],
    stream: IMStreamSession | None,
    send: Callable[[OutboundContent], Awaitable[OutboundSendResult | None]],
    *,
    should_stop: Callable[[], bool],
    on_receipt: Callable[[int, OutboundSendResult], None],
    failed_parts: frozenset[int] = frozenset(),
) -> None:
    receipts: list[OutboundSendResult] = []
    if should_stop():
        return
    if prefix.text:
        if stream is not None and stream.has_started and not stream.is_failed:
            await stream.finish(prefix.text)
        if isinstance(stream, TrackedStream):
            state = stream.delivery_state()
            if state.failure is not None and state.failure.outcome == "unknown":
                receipt = OutboundSendResult("unknown", error_kind="prefix_delivery_unknown")
            else:
                result = await recover_reply(state, prefix, send)
                receipt = OutboundSendResult(result.outcome, error_kind=result.error_kind)
        elif stream is not None and stream.has_started:
            # A legacy stream has no authoritative receipt. Do not duplicate its body.
            receipt = OutboundSendResult("unknown", error_kind="prefix_receipt_unavailable")
        else:
            receipt = await send_observed(send, prefix)
        receipts.append(receipt)
        on_receipt(-1, receipt)
    for index, part in enumerate(parts):
        if should_stop():
            return
        receipt = await send_observed(send, part)
        if index in failed_parts:
            receipt = OutboundSendResult("failed", error_kind="image_unavailable")
        receipts.append(receipt)
        on_receipt(index, receipt)
    if any(item.outcome != "delivered" for item in receipts) and not should_stop():
        await send_observed(
            send,
            replace(
                prefix,
                text="这轮图文回复未能确认完整发送，请在桌面端原会话查看。",
                text_format="plain_text",
            ),
        )
