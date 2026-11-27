"""Cooperative cancellation for read-only session HTTP requests."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from collections.abc import AsyncGenerator, Callable, Iterator
from threading import Event
from typing import Any

from fastapi import HTTPException, Request

from agent import SessionNotFoundError


class HistoryReadCancelled(Exception):
    pass


async def stream_session_records(
    operation: Callable[[Callable[[], None]], Iterator[dict[str, Any]]],
) -> AsyncGenerator[bytes, None]:
    """Bounded producer; disconnect cancellation reaches the synchronous scan."""
    released = Event()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
    loop = asyncio.get_running_loop()

    def check() -> None:
        if released.is_set():
            raise HistoryReadCancelled()

    def send(value: bytes | None) -> None:
        check()
        pending = asyncio.run_coroutine_threadsafe(queue.put(value), loop)
        try:
            while True:
                check()
                try:
                    pending.result(timeout=0.1)
                    return
                except concurrent.futures.TimeoutError:
                    pass
        finally:
            if not pending.done():
                pending.cancel()

    def produce() -> None:
        stream = None
        try:
            stream = operation(check)
            batch = bytearray()
            for record in stream:
                check()
                batch.extend((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
                if len(batch) >= 64 * 1024 or record.get("kind") == "progress":
                    send(bytes(batch))
                    batch.clear()
            if batch:
                send(bytes(batch))
        except HistoryReadCancelled:
            return
        except Exception:
            # Raw exception strings can contain local paths and private material.
            if not released.is_set():
                send(b'{"kind":"error","code":"session_export_failed"}\n')
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
            if not released.is_set():
                send(None)

    worker = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                await worker
                break
            yield chunk
    finally:
        released.set()
        worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)


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
