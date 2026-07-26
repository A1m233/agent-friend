"""Platform-neutral IM stream relay and QQ native stream tests."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from agent_bridge.protocols.im.adapters.qq_streaming import QQC2CStreamSession
from agent_bridge.protocols.im.streaming import OutboundStreamContext, StreamRelay


class StubStreamSession:
    def __init__(self) -> None:
        self.updates: list[str] = []
        self.finished: list[str] = []
        self.aborted: list[tuple[str, str]] = []
        self.failed = False

    @property
    def has_started(self) -> bool:
        return bool(self.updates)

    @property
    def is_completed(self) -> bool:
        return bool(self.finished)

    @property
    def is_failed(self) -> bool:
        return self.failed

    async def update(self, full_text: str) -> None:
        self.updates.append(full_text)

    async def finish(self, full_text: str) -> None:
        self.finished.append(full_text)

    async def abort(self, full_text: str, reason: str) -> None:
        self.aborted.append((full_text, reason))


@pytest.mark.asyncio
async def test_relay_sends_first_delta_and_coalesces_followups() -> None:
    session = StubStreamSession()
    relay = StreamRelay(session, stream_trace="trace-1", throttle_seconds=60)

    await relay.feed("hello")
    await relay.feed(" ")
    await relay.feed("world")

    assert session.updates == ["hello"]
    assert relay.full_text == "hello world"


@pytest.mark.asyncio
async def test_relay_sends_latest_snapshot_after_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10.0
    monkeypatch.setattr(
        "agent_bridge.protocols.im.streaming.time.monotonic",
        lambda: now,
    )
    session = StubStreamSession()
    relay = StreamRelay(session, stream_trace="trace-clock", throttle_seconds=0.5)

    await relay.feed("a")
    now = 10.2
    await relay.feed("b")
    now = 10.6
    await relay.feed("c")

    assert session.updates == ["a", "abc"]


@pytest.mark.asyncio
async def test_relay_drains_burst_to_one_cumulative_update() -> None:
    import asyncio

    session = StubStreamSession()
    relay = StreamRelay(session, stream_trace="trace-2", throttle_seconds=60)
    queue: asyncio.Queue[object] = asyncio.Queue()
    end = object()
    await queue.put("a")
    await queue.put("b")
    await queue.put("c")
    await queue.put(end)

    await relay.feed_from_queue(queue, end)

    assert session.updates == ["abc"]
    assert relay.full_text == "abc"


def stream_context() -> OutboundStreamContext:
    return OutboundStreamContext(
        chat_id="OPEN/ID",
        chat_scope="c2c",
        reply_to_message_id="MSG-1",
        text_format="markdown",
        stream_trace="trace-secret-safe",
        run_id="run-1",
    )


@pytest.mark.asyncio
async def test_qq_stream_reuses_id_and_increments_index_after_success() -> None:
    api = AsyncMock()
    api.request = AsyncMock(
        side_effect=[
            {"id": "STREAM-1"},
            {"id": "STREAM-1"},
            {"id": "STREAM-1"},
        ]
    )
    session = QQC2CStreamSession(
        api=api,
        bind_id="BOT-SECRET-ID",
        context=stream_context(),
        msg_seq=42,
    )

    await session.update("hello")
    await session.update("hello world")
    await session.finish("hello world!")

    calls = api.request.await_args_list
    assert len(calls) == 3
    assert calls[0].args[:2] == ("POST", "/v2/users/OPEN%2FID/stream_messages")
    first = calls[0].args[2]
    second = calls[1].args[2]
    final = calls[2].args[2]
    assert first == {
        "input_mode": "replace",
        "input_state": 1,
        "content_type": "markdown",
        "content_raw": "hello",
        "event_id": "MSG-1",
        "msg_id": "MSG-1",
        "msg_seq": 42,
        "index": 0,
    }
    assert second["stream_msg_id"] == "STREAM-1"
    assert second["index"] == 1
    assert final["stream_msg_id"] == "STREAM-1"
    assert final["index"] == 2
    assert final["input_state"] == 10
    assert final["content_raw"] == "hello world!"
    assert session.has_started is True
    assert session.is_completed is True


@pytest.mark.asyncio
async def test_qq_stream_first_failure_allows_static_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = AsyncMock()
    api.request = AsyncMock(side_effect=RuntimeError("HTTP 503 private answer body"))
    session = QQC2CStreamSession(
        api=api,
        bind_id="BOT-SECRET-ID",
        context=stream_context(),
        msg_seq=7,
    )

    with caplog.at_level(logging.INFO):
        await session.update("private answer body")

    assert session.has_started is False
    assert session.is_failed is True
    assert "content_len=19" in caplog.text
    assert "private answer body" not in caplog.text
    assert "BOT-SECRET-ID" not in caplog.text


@pytest.mark.asyncio
async def test_qq_stream_mid_failure_retries_latest_text_as_done() -> None:
    api = AsyncMock()
    api.request = AsyncMock(
        side_effect=[
            {"id": "STREAM-2"},
            RuntimeError("temporary failure"),
            {"id": "STREAM-2"},
        ]
    )
    session = QQC2CStreamSession(
        api=api,
        bind_id="BOT",
        context=stream_context(),
        msg_seq=9,
    )

    await session.update("first")
    await session.update("first second")
    await session.finish("first second final")

    assert session.has_started is True
    assert session.is_failed is True
    assert session.is_completed is True
    failed_payload = api.request.await_args_list[1].args[2]
    final_payload = api.request.await_args_list[2].args[2]
    assert failed_payload["index"] == 1
    assert final_payload["index"] == 1
    assert final_payload["input_state"] == 10
    assert final_payload["content_raw"] == "first second final"
