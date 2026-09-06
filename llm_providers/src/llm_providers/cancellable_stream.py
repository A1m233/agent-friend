"""Provider-neutral bridge from async streams to the synchronous runtime.

The runtime consumes synchronous iterators, while an async request can be
cancelled even before response headers arrive.  Keep that concurrency detail in
one transport helper so provider adapters only own request construction and
resource cleanup.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

_CANCEL_POLL_SECONDS = 0.05
_STREAM_END = object()


class StreamIdleTimeoutExceeded(TimeoutError):
    """Raised when a provider stream makes no observable progress for too long."""

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(f"LLM stream was idle for {timeout_seconds:g}s")
        self.timeout_seconds = timeout_seconds


class AsyncStreamSource(Protocol):
    """Provider adapter contract consumed by :class:`CancellableAsyncStream`."""

    request_id: str | None

    async def open(self) -> AsyncIterator[Any]:
        """Open the upstream response and return its async chunk iterator."""

    async def close(self) -> None:
        """Release any response/client resources, including a partial open."""


@dataclass(frozen=True)
class _StreamFailure:
    error: BaseException


class CancellableAsyncStream:
    """Expose an async provider stream as a cancellable synchronous iterator."""

    def __init__(
        self,
        source: AsyncStreamSource,
        cancel_check: Callable[[], None],
        *,
        timeout_seconds: float | None = None,
        thread_name: str = "llm-cancellable-stream",
    ) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.request_id: str | None = None
        self.request_witness: Any = None
        self._source = source
        self._cancel_check = cancel_check
        self._items: queue.Queue[object] = queue.Queue()
        self._opened = threading.Event()
        self._opened_successfully = False
        self._open_error: BaseException | None = None
        self._closed = threading.Event()
        self._done = threading.Event()
        self._timeout_seconds = timeout_seconds
        self._last_activity_at = time.monotonic()
        self._task_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=thread_name,
        )
        self._thread.start()

    def wait_until_open(self) -> None:
        """Wait for response headers while continuing to observe cancellation."""
        try:
            while not self._opened.wait(_CANCEL_POLL_SECONDS):
                self._check_control()
            self._check_control()
        except BaseException:
            self.close()
            raise
        if self._open_error is not None:
            raise self._open_error
        if not self._opened_successfully:
            raise RuntimeError("LLM stream closed before response headers arrived")

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        while True:
            self._check_control()
            try:
                item = self._items.get(timeout=_CANCEL_POLL_SECONDS)
            except queue.Empty:
                if self._done.is_set():
                    raise StopIteration from None
                continue
            if item is _STREAM_END:
                raise StopIteration
            if isinstance(item, _StreamFailure):
                raise item.error
            return item

    def _check_control(self) -> None:
        try:
            self._cancel_check()
            timeout_seconds = self._timeout_seconds
            if (
                timeout_seconds is not None
                and time.monotonic() - self._last_activity_at >= timeout_seconds
            ):
                raise StreamIdleTimeoutExceeded(timeout_seconds)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._task_lock:
            loop = self._loop
            task = self._task
        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(self._cancel_pending_request)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)

    def _cancel_pending_request(self) -> None:
        # Recheck on the event-loop thread: completion may have entered cleanup
        # after close() queued this callback. Never interrupt resource release.
        with self._task_lock:
            task = self._task
        if task is not None and not task.done():
            task.cancel()

    def _run(self) -> None:
        asyncio.run(self._produce())

    async def _produce(self) -> None:
        with self._task_lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        try:
            if self._closed.is_set():
                return
            response = await self._source.open()
            if self._closed.is_set():
                return
            self.request_id = self._source.request_id
            self.request_witness = getattr(self._source, "request_witness", None)
            self._last_activity_at = time.monotonic()
            self._opened_successfully = True
            self._opened.set()
            async for chunk in response:
                if self._closed.is_set():
                    break
                self._last_activity_at = time.monotonic()
                self._items.put(chunk)
        except asyncio.CancelledError as exc:
            if not self._closed.is_set():
                self._record_failure(exc)
        except BaseException as exc:
            self._record_failure(exc)
        finally:
            with self._task_lock:
                self._task = None
                self._loop = None
            with suppress(Exception):
                await self._source.close()
            self._opened.set()
            self._items.put(_STREAM_END)
            self._done.set()

    def _record_failure(self, error: BaseException) -> None:
        if not self._opened_successfully:
            self._open_error = error
            self._opened.set()
            return
        self._items.put(_StreamFailure(error))


def open_cancellable_stream(
    source: AsyncStreamSource,
    cancel_check: Callable[[], None],
    *,
    timeout_seconds: float | None = None,
    thread_name: str = "llm-cancellable-stream",
) -> CancellableAsyncStream:
    """Open ``source`` without making response-header wait uninterruptible."""
    stream = CancellableAsyncStream(
        source,
        cancel_check,
        timeout_seconds=timeout_seconds,
        thread_name=thread_name,
    )
    try:
        stream.wait_until_open()
    except BaseException:
        stream.close()
        raise
    return stream
