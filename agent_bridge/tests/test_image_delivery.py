from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from agent_bridge.protocols.im.content import OutboundContent, OutboundImage
from agent_bridge.protocols.im.delivery.images import deliver_images
from agent_bridge.protocols.im.provider import OutboundSendResult


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["delivered", "failed", "unknown"])
async def test_image_order_and_no_retry(outcome: Literal["delivered", "failed", "unknown"]) -> None:
    prefix = OutboundContent(chat_id="user", chat_scope="c2c", text="before")
    picture = OutboundContent(
        chat_id="user",
        chat_scope="c2c",
        text="",
        image=OutboundImage(Path("image.png"), "image.png"),
    )
    after = OutboundContent(chat_id="user", chat_scope="c2c", text="after")
    send = AsyncMock(
        side_effect=[
            OutboundSendResult("delivered"),
            OutboundSendResult(outcome),
            OutboundSendResult("delivered"),
            OutboundSendResult("delivered"),
        ]
    )
    receipts = []
    await deliver_images(
        prefix,
        [picture, after],
        None,
        send,
        should_stop=lambda: False,
        on_receipt=lambda index, receipt: receipts.append((index, receipt.outcome)),
    )
    assert [call.args[0] for call in send.await_args_list[:3]] == [prefix, picture, after]
    assert receipts == [(-1, "delivered"), (0, outcome), (1, "delivered")]
    assert send.await_count == (3 if outcome == "delivered" else 4)


@pytest.mark.asyncio
async def test_cancel_stops_remaining_parts() -> None:
    stopped = False

    async def send(content: OutboundContent) -> OutboundSendResult:
        nonlocal stopped
        stopped = True
        return OutboundSendResult("delivered")

    mock = AsyncMock(side_effect=send)
    content = OutboundContent(chat_id="user", chat_scope="c2c", text="part")
    await deliver_images(
        content,
        [content, content],
        None,
        mock,
        should_stop=lambda: stopped,
        on_receipt=lambda *_: None,
    )
    assert mock.await_count == 1
