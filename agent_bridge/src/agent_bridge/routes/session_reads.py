"""Cooperative cancellation for read-only session HTTP requests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from threading import Event
from typing import Any

from fastapi import HTTPException, Request

from agent import SessionNotFoundError


class HistoryReadCancelled(Exception):
    pass


async def read_session(request: Request, operation: Callable[[Callable[[], None]], Any]) -> Any:
    released = Event()

    def check() -> None:
        if released.is_set():
            raise HistoryReadCancelled()

    work = asyncio.create_task(asyncio.to_thread(operation, check))
    try:
        while not work.done():
            if await request.is_disconnected():
                released.set()
                raise HTTPException(status_code=499, detail={"error": "cancelled"})
            await asyncio.wait({work}, timeout=0.1)
        return work.result()
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "history_changed",
                "message": "历史已更新，请重新打开消息。",
            },
        ) from exc
    finally:
        released.set()
        # The worker cooperatively stops at the index boundary. Retrieve its
        # eventual exception without waiting on unrelated requests or tasks.
        if not work.done():
            work.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
