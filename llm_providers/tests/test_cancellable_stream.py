from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest
from llm_providers.cancellable_stream import (
    StreamIdleTimeoutExceeded,
    open_cancellable_stream,
)


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
