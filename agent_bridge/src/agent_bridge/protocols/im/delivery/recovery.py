"""One static continuation after a failed stream, never a new model execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from ..content import OutboundContent
from ..provider import OutboundSendResult
from .models import ReplyDeliveryResult, StreamDeliveryState

logger = logging.getLogger(__name__)
Send = Callable[[OutboundContent], Awaitable[OutboundSendResult | None]]


async def send_observed(send: Send, content: OutboundContent) -> OutboundSendResult:
    try:
        result = await send(content)
    except Exception:
        # Adapter details can contain private payloads: log only the outcome here.
        return OutboundSendResult("unknown", error_kind="send_exception")
    return result or OutboundSendResult("unknown", error_kind="missing_receipt")


async def recover_reply(
    state: StreamDeliveryState, content: OutboundContent, send: Send
) -> ReplyDeliveryResult:
    """Cover exactly the unconfirmed suffix, at most once per invocation/turn."""
    accepted = state.accepted_text
    ids = (state.message_id,) if accepted and state.message_id else ()
    failure = state.failure
    duplicate_risk = failure is not None and failure.outcome == "unknown"
    base = ReplyDeliveryResult(
        "failed",
        len(accepted),
        state.terminal_accepted,
        duplicate_risk=duplicate_risk,
        error_kind=failure.kind if failure else None,
        message_ids=ids,
    )
    if not content.text.startswith(accepted):
        return replace(base, error_kind="content_diverged")
    remaining = content.text[len(accepted) :]
    if not remaining:
        return replace(base, outcome="delivered")
    if failure is not None and not failure.recoverable:
        return replace(base, outcome=failure.outcome)
    # Plain text avoids treating a cut inside Markdown as a new Markdown document.
    # Preserve every unconfirmed character, including whitespace, URLs and Unicode.
    continuation = replace(
        content,
        text=("接上条：\n" if accepted else "") + remaining,
        text_format="plain_text" if accepted else content.text_format,
        controls=None,
    )
    receipt = await send_observed(send, continuation)
    if receipt.outcome == "delivered":
        return replace(
            base,
            outcome="delivered",
            accepted_chars=len(content.text),
            recovery_attempted=True,
            message_ids=ids
            + ((receipt.provider_message_id,) if receipt.provider_message_id else ()),
        )
    return replace(
        base,
        outcome=receipt.outcome,
        recovery_attempted=True,
        duplicate_risk=duplicate_risk or receipt.outcome == "unknown",
        error_kind=receipt.error_kind or "recovery_failed",
    )
