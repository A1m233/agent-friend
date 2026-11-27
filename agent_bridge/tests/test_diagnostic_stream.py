from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from threading import Event
from typing import Any

import pytest
from agent_bridge.routes.session_reads import stream_session_records


@pytest.mark.asyncio
async def test_stream_producer_failure_is_terminal_without_private_exception_text() -> None:
    def failed(check: Callable[[], None]) -> Iterator[dict[str, Any]]:
        check()
        raise OSError("private-local-path")

    body = [chunk async for chunk in stream_session_records(failed)]
    assert len(body) == 1
    assert json.loads(body[0]) == {"kind": "error", "code": "session_export_failed"}


@pytest.mark.asyncio
async def test_disconnect_stops_scan_and_releases_generator() -> None:
    stopped = Event()

    def produce(check: Callable[[], None]) -> Iterator[dict[str, Any]]:
        try:
            yield {"kind": "progress"}
            while True:
                check()
                stopped.wait(0.005)
        finally:
            stopped.set()

    stream = stream_session_records(produce)
    assert json.loads(await anext(stream)) == {"kind": "progress"}
    await stream.aclose()
    assert await asyncio.to_thread(stopped.wait, 1)
