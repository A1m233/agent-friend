from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from agent_bridge.protocols.im.coalescing import QQAttachmentTextCoalescer
from agent_bridge.protocols.im.coordinator import IMBindingCoordinator
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.state import IMBindingKey
from qqbot_agent_sdk.dto import MessageAttachment, MsgElement


class NeverSleep:
    def __init__(self) -> None:
        self.futures: list[asyncio.Future[None]] = []

    async def __call__(self, _delay: float) -> None:
        future = asyncio.get_running_loop().create_future()
        self.futures.append(future)
        await future


def event(
    message_id: str,
    *,
    content: str = "",
    chat_id: str = "OPENID-USER",
    attachments: list[MessageAttachment] | None = None,
    msg_elements: list[MsgElement] | None = None,
    timestamp: str = "2026-08-26T10:00:00Z",
    raw: dict[str, object] | None = None,
) -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id=chat_id,
        user_id=chat_id,
        chat_scope="c2c",
        content=content,
        message_id=message_id,
        timestamp=timestamp,
        message_type=0,
        attachments=attachments or [],
        msg_elements=msg_elements or [],
        raw=raw,
    )


def attachment(
    filename: str,
    content_type: str,
    *,
    voice_wav_url: str = "",
) -> MessageAttachment:
    return MessageAttachment(
        url=f"https://cdn.example.test/{filename}",
        filename=filename,
        content_type=content_type,
        voice_wav_url=voice_wav_url,
    )


def key(chat_id: str = "OPENID-USER") -> IMBindingKey:
    return IMBindingKey("qq", "OPENID-BOT", "c2c", chat_id)


async def no_timeout(_key: IMBindingKey, _token: str) -> None:
    raise AssertionError("timeout should not fire in this test")


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("photo.jpg", "image/jpeg"),
        ("photo.png", "image/png"),
        ("photo.webp", "image/webp"),
        ("report.pdf", "application/pdf"),
        ("notes.txt", "text/plain"),
        ("unknown.bin", "application/octet-stream"),
    ],
)
async def test_supported_or_unknown_attachment_only_event_is_held(
    filename: str,
    content_type: str,
) -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    provider = object()
    inbound = event("ATT-1", attachments=[attachment(filename, content_type)])

    decision = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=inbound,
        on_timeout=no_timeout,
    )

    assert decision.dispatches == ()
    assert decision.held_message_id == "ATT-1"
    assert coalescer.pending_count == 1
    assert coordinator.has_pending_turn(key())
    assert await coalescer.cancel_all() == ("ATT-1",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        attachment("sticker.gif", "image/gif"),
        attachment("voice.wav", "audio/wav", voice_wav_url="https://cdn.example.test/v.wav"),
        attachment("movie.mp4", "video/mp4"),
        attachment(
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
async def test_explicitly_unsupported_attachment_bypasses_wait(
    item: MessageAttachment,
) -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    inbound = event("ATT-1", attachments=[item])

    decision = coalescer.offer(
        provider=object(),
        binding_key=key(),
        session_id="SESSION-A",
        event=inbound,
        on_timeout=no_timeout,
    )

    assert [item.reason for item in decision.dispatches] == ["direct"]
    assert coalescer.pending_count == 0
    assert not coordinator.has_pending_turn(key())


@pytest.mark.asyncio
async def test_non_qq_or_non_c2c_attachment_bypasses_wait() -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    inbound = event("ATT-1", attachments=[attachment("photo.png", "image/png")])

    non_qq = coalescer.offer(
        provider=object(),
        binding_key=IMBindingKey("feishu", "BOT", "c2c", "USER"),
        session_id="SESSION-A",
        event=inbound,
        on_timeout=no_timeout,
    )
    group_event = replace(inbound, chat_scope="group", chat_id="GROUP")
    non_c2c = coalescer.offer(
        provider=object(),
        binding_key=IMBindingKey("qq", "BOT", "group", "GROUP"),
        session_id="SESSION-A",
        event=group_event,
        on_timeout=no_timeout,
    )

    assert [item.reason for item in non_qq.dispatches] == ["direct"]
    assert [item.reason for item in non_c2c.dispatches] == ["direct"]
    assert coalescer.pending_count == 0


@pytest.mark.asyncio
async def test_first_pure_text_merges_pending_attachment_and_uses_text_identity() -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    provider = object()
    image = attachment("photo.png", "image/png")
    referenced = attachment("report.pdf", "application/pdf")
    attachment_event = event(
        "ATT-1",
        attachments=[image],
        msg_elements=[MsgElement(msg_idx="1", attachments=[referenced])],
    )
    text_event = event("TXT-1", content="  帮我看看  ")

    coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=attachment_event,
        on_timeout=no_timeout,
    )
    decision = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=text_event,
        on_timeout=no_timeout,
    )

    assert len(decision.dispatches) == 1
    dispatch = decision.dispatches[0]
    assert dispatch.reason == "merged"
    assert dispatch.source_message_ids == ("ATT-1", "TXT-1")
    assert dispatch.event.message_id == "TXT-1"
    assert dispatch.event.content == "  帮我看看  "
    assert dispatch.event.attachments == [image]
    assert dispatch.event.msg_elements[0].attachments == [referenced]
    assert coalescer.pending_count == 0
    assert not coordinator.has_pending_turn(key())


@pytest.mark.asyncio
async def test_fragmented_attachments_append_to_one_logical_batch() -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    provider = object()

    first = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("ATT-1", attachments=[attachment("one.png", "image/png")]),
        on_timeout=no_timeout,
    )
    second = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("ATT-2", attachments=[attachment("two.png", "image/png")]),
        on_timeout=no_timeout,
    )

    assert first.held_message_id == "ATT-1"
    assert second.dispatches == ()
    assert second.held_message_id == "ATT-2"
    assert second.held_source_message_ids == ("ATT-1", "ATT-2")
    assert second.held_attachment_count == 2
    assert second.held_reason == "appended"
    assert coalescer.pending_count == 1
    assert coordinator.has_pending_turn(key())

    merged = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("TXT-1", content="一起看"),
        on_timeout=no_timeout,
    )

    assert len(merged.dispatches) == 1
    dispatch = merged.dispatches[0]
    assert dispatch.source_message_ids == ("ATT-1", "ATT-2", "TXT-1")
    assert dispatch.event.message_id == "TXT-1"
    assert [item.filename for item in dispatch.event.attachments] == ["one.png", "two.png"]
    assert coalescer.pending_count == 0


@pytest.mark.asyncio
async def test_real_incident_timing_keeps_five_images_and_caption_in_one_batch() -> None:
    coordinator = IMBindingCoordinator()
    clock = MutableClock()
    coalescer = QQAttachmentTextCoalescer(
        coordinator,
        window_seconds=3.0,
        sleep=NeverSleep(),
        clock=clock,
    )
    provider = object()

    image_times = (0.0, 0.036, 1.554, 1.564, 2.563)
    for index, observed_at in enumerate(image_times, start=1):
        clock.now = observed_at
        decision = coalescer.offer(
            provider=provider,
            binding_key=key(),
            session_id="SESSION-A",
            event=event(
                f"ATT-{index}",
                attachments=[attachment(f"{index}.png", "image/png")],
            ),
            on_timeout=no_timeout,
        )
        assert decision.dispatches == ()

    clock.now = 3.056
    merged = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("TXT-5", content="都看8月"),
        on_timeout=no_timeout,
    )

    assert len(merged.dispatches) == 1
    dispatch = merged.dispatches[0]
    assert dispatch.source_message_ids == (
        "ATT-1",
        "ATT-2",
        "ATT-3",
        "ATT-4",
        "ATT-5",
        "TXT-5",
    )
    assert [item.filename for item in dispatch.event.attachments] == [
        "1.png",
        "2.png",
        "3.png",
        "4.png",
        "5.png",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image_times",
    [
        (0.0, 0.038, 0.074, 0.579, 0.613),
        (0.0, 0.522),
    ],
)
async def test_observed_one_send_bursts_stay_in_one_batch(
    image_times: tuple[float, ...],
) -> None:
    coordinator = IMBindingCoordinator()
    clock = MutableClock()
    coalescer = QQAttachmentTextCoalescer(
        coordinator,
        window_seconds=3.0,
        sleep=NeverSleep(),
        clock=clock,
    )
    provider = object()

    decision = None
    for index, observed_at in enumerate(image_times, start=1):
        clock.now = observed_at
        decision = coalescer.offer(
            provider=provider,
            binding_key=key(),
            session_id="SESSION-A",
            event=event(
                f"ATT-{index}",
                attachments=[attachment(f"{index}.png", "image/png")],
            ),
            on_timeout=no_timeout,
        )
        assert decision.dispatches == ()

    assert decision is not None
    assert decision.held_source_message_ids == tuple(
        f"ATT-{index}" for index in range(1, len(image_times) + 1)
    )
    assert decision.held_attachment_count == len(image_times)
    assert await coalescer.cancel_all() == decision.held_source_message_ids


@pytest.mark.asyncio
async def test_observed_independent_send_timing_starts_five_separate_batches() -> None:
    coordinator = IMBindingCoordinator()
    clock = MutableClock()
    coalescer = QQAttachmentTextCoalescer(
        coordinator,
        window_seconds=3.0,
        sleep=NeverSleep(),
        clock=clock,
    )
    provider = object()
    observed_times = (0.0, 4.543, 8.077, 11.619, 14.965)
    for index, observed_at in enumerate(observed_times, start=1):
        clock.now = observed_at
        decision = coalescer.offer(
            provider=provider,
            binding_key=key(),
            session_id="SESSION-A",
            event=event(
                f"ATT-{index}",
                attachments=[attachment(f"{index}.png", "image/png")],
            ),
            on_timeout=no_timeout,
        )
        expected_dispatches = [] if index == 1 else ["timeout"]
        assert [item.reason for item in decision.dispatches] == expected_dispatches
        if decision.dispatches:
            assert decision.dispatches[0].source_message_ids == (f"ATT-{index - 1}",)
        assert decision.held_source_message_ids == (f"ATT-{index}",)

    assert await coalescer.cancel_all() == ("ATT-5",)


@pytest.mark.asyncio
async def test_default_without_hard_limit_keeps_batch_open_while_attachments_arrive() -> None:
    coordinator = IMBindingCoordinator()
    clock = MutableClock()
    coalescer = QQAttachmentTextCoalescer(
        coordinator,
        window_seconds=3.0,
        sleep=NeverSleep(),
        clock=clock,
    )
    provider = object()

    for index, observed_at in enumerate((0.0, 2.9, 5.8, 8.7), start=1):
        clock.now = observed_at
        decision = coalescer.offer(
            provider=provider,
            binding_key=key(),
            session_id="SESSION-A",
            event=event(
                f"ATT-{index}",
                attachments=[attachment(f"{index}.png", "image/png")],
            ),
            on_timeout=no_timeout,
        )
        assert decision.dispatches == ()

    clock.now = 8.8
    merged = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("TXT-4", content="一起看"),
        on_timeout=no_timeout,
    )

    assert len(merged.dispatches) == 1
    assert merged.dispatches[0].source_message_ids == (
        "ATT-1",
        "ATT-2",
        "ATT-3",
        "ATT-4",
        "TXT-4",
    )
    assert coalescer.max_batch_seconds is None


@pytest.mark.asyncio
async def test_hard_limit_cannot_be_extended_by_more_attachments() -> None:
    coordinator = IMBindingCoordinator()
    clock = MutableClock()
    coalescer = QQAttachmentTextCoalescer(
        coordinator,
        window_seconds=3.0,
        max_batch_seconds=5.0,
        sleep=NeverSleep(),
        clock=clock,
    )
    provider = object()
    coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("ATT-1", attachments=[attachment("one.png", "image/png")]),
        on_timeout=no_timeout,
    )
    clock.now = 2.9
    appended = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("ATT-2", attachments=[attachment("two.png", "image/png")]),
        on_timeout=no_timeout,
    )
    assert appended.dispatches == ()

    clock.now = 4.9
    appended_again = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("ATT-3", attachments=[attachment("three.png", "image/png")]),
        on_timeout=no_timeout,
    )
    assert appended_again.dispatches == ()

    clock.now = 5.1
    after_limit = coalescer.offer(
        provider=provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("TXT-LATE", content="已经是下一条"),
        on_timeout=no_timeout,
    )

    assert [item.reason for item in after_limit.dispatches] == ["timeout", "direct"]
    assert after_limit.dispatches[0].source_message_ids == ("ATT-1", "ATT-2", "ATT-3")
    assert after_limit.dispatches[1].event.message_id == "TXT-LATE"


@pytest.mark.asyncio
async def test_other_chat_cannot_consume_pending_attachment() -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    provider = object()
    coalescer.offer(
        provider=provider,
        binding_key=key("CHAT-A"),
        session_id="SESSION-A",
        event=event(
            "ATT-1",
            chat_id="CHAT-A",
            attachments=[attachment("one.png", "image/png")],
        ),
        on_timeout=no_timeout,
    )

    decision = coalescer.offer(
        provider=provider,
        binding_key=key("CHAT-B"),
        session_id="SESSION-A",
        event=event("TXT-1", chat_id="CHAT-B", content="不是配文"),
        on_timeout=no_timeout,
    )

    assert [item.reason for item in decision.dispatches] == ["direct"]
    assert coalescer.pending_count == 1
    assert await coalescer.cancel_all() == ("ATT-1",)


@pytest.mark.asyncio
async def test_provider_change_drops_old_pending_instead_of_cross_merging() -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    old_provider = object()
    new_provider = object()
    coalescer.offer(
        provider=old_provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("ATT-OLD", attachments=[attachment("one.png", "image/png")]),
        on_timeout=no_timeout,
    )

    decision = coalescer.offer(
        provider=new_provider,
        binding_key=key(),
        session_id="SESSION-A",
        event=event("TXT-NEW", content="new provider text"),
        on_timeout=no_timeout,
    )

    assert decision.dropped_message_ids == ("ATT-OLD",)
    assert [item.event.message_id for item in decision.dispatches] == ["TXT-NEW"]
    assert coalescer.pending_count == 0
    assert not coordinator.has_pending_turn(key())


@pytest.mark.asyncio
async def test_cancel_provider_releases_only_its_reservations() -> None:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(coordinator, sleep=NeverSleep())
    provider_a = object()
    provider_b = object()
    for provider, chat_id, message_id in (
        (provider_a, "CHAT-A", "ATT-A"),
        (provider_b, "CHAT-B", "ATT-B"),
    ):
        coalescer.offer(
            provider=provider,
            binding_key=key(chat_id),
            session_id="SESSION-A",
            event=event(
                message_id,
                chat_id=chat_id,
                attachments=[attachment(f"{chat_id}.png", "image/png")],
            ),
            on_timeout=no_timeout,
        )

    assert coalescer.cancel_provider(provider_a) == ("ATT-A",)
    assert not coordinator.has_pending_turn(key("CHAT-A"))
    assert coordinator.has_pending_turn(key("CHAT-B"))
    assert await coalescer.cancel_all() == ("ATT-B",)


def test_stale_reservation_token_cannot_release_a_new_pending_turn() -> None:
    coordinator = IMBindingCoordinator()
    first = coordinator.reserve_pending_turn(key())
    assert coordinator.release_pending_turn(first)
    second = coordinator.reserve_pending_turn(key())

    assert not coordinator.release_pending_turn(first)
    assert coordinator.has_pending_turn(key())
    assert coordinator.release_pending_turn(second)
    assert not coordinator.has_pending_turn(key())
