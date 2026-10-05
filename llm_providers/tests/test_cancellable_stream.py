from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest
from llm_providers.cancellable_stream import (
    StreamIdleTimeoutExceeded,
    open_cancellable_stream,
)
from llm_providers.timeouts import ModelCallDeadlineExceeded, controlled_cancel


class _StallingSource:
    request_id: str | None = "stalling-request"

    def __init__(self) -> None:
        self.closed = threading.Event()

    async def open(self) -> AsyncIterator[object]:
        async def chunks() -> AsyncIterator[object]:
            yield object()
            await asyncio.sleep(60)

        return chunks()

    async def close(self) -> None:
        self.closed.set()


def test_stream_idle_timeout_is_measured_from_last_provider_event() -> None:
    source = _StallingSource()
    stream = open_cancellable_stream(
        source,
        lambda: None,
        timeout_seconds=0.05,
    )

    next(stream)
    with pytest.raises(StreamIdleTimeoutExceeded, match=r"idle for 0\.05s"):
        next(stream)

    assert source.closed.wait(1)


def test_close_does_not_cancel_cleanup_after_request_has_completed() -> None:
    cleanup_started = threading.Event()
    cleaned_up = threading.Event()

    class CompletedSource:
        request_id: str | None = None

        async def open(self) -> AsyncIterator[object]:
            async def chunks() -> AsyncIterator[object]:
                yield "completed"

            return chunks()

        async def close(self) -> None:
            cleanup_started.set()
            await asyncio.sleep(0.1)
            cleaned_up.set()

    stream = open_cancellable_stream(CompletedSource(), lambda: None)
    assert next(stream) == "completed"
    assert cleanup_started.wait(1)

    stream.close()

    assert cleaned_up.is_set()
    assert not stream._thread.is_alive()


def test_heartbeats_reset_idle_but_do_not_override_deadline_or_cancel() -> None:
    class Heartbeats(_StallingSource):
        async def open(self) -> AsyncIterator[object]:
            async def chunks() -> AsyncIterator[object]:
                while True:
                    yield {"type": "keepalive"}
                    await asyncio.sleep(0.01)

            return chunks()

    import time

    source = Heartbeats()
    stream = open_cancellable_stream(source, lambda: None, timeout_seconds=0.5)
    start = time.monotonic()
    while time.monotonic() - start < 0.6:
        assert next(stream) == {"type": "keepalive"}
    stream.close()
    assert source.closed.wait(1)

    source = Heartbeats()
    cancel = controlled_cancel(None, time.monotonic() + 0.1)
    assert cancel is not None
    stream = open_cancellable_stream(source, cancel, timeout_seconds=0.5)
    with pytest.raises(ModelCallDeadlineExceeded):
        list(stream)
    assert source.closed.wait(1)
