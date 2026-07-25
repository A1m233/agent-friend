"""Per-IM-binding turn and mutation coordination."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .state import IMBindingKey

__all__ = ["IMBindingBusyError", "IMBindingCoordinator"]


class IMBindingBusyError(RuntimeError):
    pass


class IMBindingCoordinator:
    """Queue ordinary inbound turns while rejecting control mutations during a turn."""

    def __init__(self) -> None:
        self._locks: dict[IMBindingKey, asyncio.Lock] = {}

    def _lock_for(self, key: IMBindingKey) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @asynccontextmanager
    async def turn(self, key: IMBindingKey) -> AsyncIterator[None]:
        lock = self._lock_for(key)
        async with lock:
            yield

    @asynccontextmanager
    async def try_mutation(self, key: IMBindingKey) -> AsyncIterator[None]:
        lock = self._lock_for(key)
        if lock.locked():
            raise IMBindingBusyError("IM binding is busy")
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
