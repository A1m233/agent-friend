"""QQ reply recovery: receipt boundaries, uncertainty and bounded requests."""

from unittest.mock import AsyncMock

import pytest
from agent_bridge.protocols.im.adapters.qq_delivery_errors import classify_qq_failure
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.delivery.models import DeliveryFailure, StreamDeliveryState
from agent_bridge.protocols.im.delivery.recovery import recover_reply
from agent_bridge.protocols.im.provider import OutboundSendResult


def content(text: str) -> OutboundContent:
    return OutboundContent(
        chat_id="user",
        chat_scope="c2c",
        reply_to_message_id="inbound",
        text=text,
        text_format="markdown",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "你好😀", "```python\npri", "[链接](https://exam"])
async def test_recovery_preserves_exact_suffix_and_inbound(prefix: str) -> None:
    state = StreamDeliveryState(
        accepted_text=prefix,
        message_id="old" if prefix else None,
        failure=DeliveryFailure("failed", "stream_immutable", True),
    )
    send = AsyncMock(return_value=OutboundSendResult("delivered", "new"))
    result = await recover_reply(state, content(prefix + "后续\n代码 URL)"), send)
    send.assert_awaited_once()
    assert send.await_args is not None
    sent = send.await_args.args[0]
    assert sent.text == ("接上条：\n" if prefix else "") + "后续\n代码 URL)"
    assert sent.reply_to_message_id == "inbound"
    assert sent.text_format == ("plain_text" if prefix else "markdown")
    assert result.outcome == "delivered"
    assert result.accepted_chars == len(prefix + "后续\n代码 URL)")
    assert result.message_ids == (("old", "new") if prefix else ("new",))


@pytest.mark.asyncio
async def test_done_failure_after_full_receipt_does_not_duplicate_body() -> None:
    send = AsyncMock()
    result = await recover_reply(
        StreamDeliveryState("whole", "old", failure=DeliveryFailure("unknown", "timeout", True)),
        content("whole"),
        send,
    )
    send.assert_not_awaited()
    assert result.outcome == "delivered"
    assert not result.terminal_accepted
    assert result.duplicate_risk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        OutboundSendResult("failed", error_kind="platform_rejected"),
        OutboundSendResult("unknown", error_kind="timeout"),
        None,
    ],
)
async def test_recovery_never_retries_failed_or_unknown_send(receipt: object) -> None:
    send = AsyncMock(return_value=receipt)
    result = await recover_reply(
        StreamDeliveryState("a", "old", failure=DeliveryFailure("unknown", "timeout", True)),
        content("abcd"),
        send,
    )
    send.assert_awaited_once()
    assert result.accepted_chars == 1
    assert result.outcome != "delivered"
    assert result.duplicate_risk


@pytest.mark.asyncio
async def test_unknown_old_frame_followed_by_confirmed_recovery_keeps_risk() -> None:
    result = await recover_reply(
        StreamDeliveryState("a", "old", failure=DeliveryFailure("unknown", "timeout", True)),
        content("abc"),
        AsyncMock(return_value=OutboundSendResult("delivered", "new")),
    )
    assert result.outcome == "delivered"
    assert result.duplicate_risk


@pytest.mark.asyncio
async def test_permission_rejection_and_divergent_content_are_not_resent() -> None:
    send = AsyncMock()
    for state, text, reason in [
        (
            StreamDeliveryState(failure=DeliveryFailure("failed", "http_rejected", False)),
            "body",
            "http_rejected",
        ),
        (StreamDeliveryState("different", "old"), "body", "content_diverged"),
    ]:
        result = await recover_reply(state, content(text), send)
        assert result.error_kind == reason
        assert result.outcome == "failed"
    send.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "outcome", "recoverable"),
    [
        (
            RuntimeError(
                "QQ Bot API error [404] /v2/users/user/stream_messages: 已经提交的消息内容不可修改"
            ),
            "failed",
            True,
        ),
        (
            RuntimeError(
                "QQ Bot API error [400] /v2/users/user/stream_messages: 流式消息已经结束，不能继续发送"
            ),
            "failed",
            True,
        ),
        (
            RuntimeError(
                "QQ Bot API error [400] /v2/users/user/stream_messages: 请求参数index需要递增"
            ),
            "failed",
            False,
        ),
        (
            RuntimeError("QQ Bot API error [404] /v2/users/user/stream_messages: not found"),
            "failed",
            False,
        ),
        (
            RuntimeError("QQ Bot API error [403] /v2/users/user/stream_messages: forbidden"),
            "failed",
            False,
        ),
        (
            RuntimeError("QQ Bot API error [503] /v2/users/user/stream_messages: unavailable"),
            "unknown",
            True,
        ),
        (TimeoutError("timeout"), "unknown", True),
        (RuntimeError("already committed"), "unknown", True),
        ({"code": 11001, "message": "denied"}, "failed", False),
        ({}, "unknown", True),
    ],
)
def test_platform_failure_classification(value: object, outcome: str, recoverable: bool) -> None:
    failure = classify_qq_failure(value, stream=True)
    assert failure.outcome == outcome
    assert failure.recoverable == recoverable


def test_stream_ended_message_does_not_enable_static_resend() -> None:
    failure = classify_qq_failure(
        RuntimeError(
            "QQ Bot API error [400] /v2/users/user/messages: 流式消息已经结束，不能继续发送"
        )
    )
    assert failure.outcome == "failed"
    assert failure.recoverable is False
