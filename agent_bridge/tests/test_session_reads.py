from __future__ import annotations

import asyncio
from threading import Event
from typing import Any

import pytest
from agent_bridge.routes.session_reads import HistoryReadCancelled, read_session
from fastapi import HTTPException


def test_disconnecting_history_reader_cooperatively_stops_its_worker() -> None:
    started = Event()
    stopped = Event()

    class Request:
        async def is_disconnected(self) -> bool:
            await asyncio.to_thread(started.wait, 1)
            return True

    def operation(check: Any) -> None:
        started.set()
        try:
            while not stopped.wait(0.01):
                check()
        except HistoryReadCancelled:
            stopped.set()
            raise

    async def run() -> None:
        with pytest.raises(HTTPException) as error:
            await read_session(Request(), operation)  # type: ignore[arg-type]
        assert error.value.status_code == 499
        assert await asyncio.to_thread(stopped.wait, 1)

    asyncio.run(run())
