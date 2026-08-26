"""Per-IM-binding turn and mutation coordination."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from .state import IMBindingKey

__all__ = ["IMBindingBusyError", "IMBindingCoordinator", "IMBindingReservation"]


class IMBindingBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class IMBindingReservation:
    """Opaque ownership token for a pending inbound turn."""

    key: IMBindingKey
    token: str


class IMBindingCoordinator:
    """Queue ordinary inbound turns while rejecting control mutations during a turn."""

    def __init__(self) -> None:
        self._locks: dict[IMBindingKey, asyncio.Lock] = {}
        self._pending_reservations: dict[IMBindingKey, set[str]] = {}

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

    def reserve_pending_turn(self, key: IMBindingKey) -> IMBindingReservation:
        """Keep control mutations busy while an inbound turn waits for more input."""

        reservation = IMBindingReservation(key=key, token=uuid4().hex)
        self._pending_reservations.setdefault(key, set()).add(reservation.token)
        return reservation

    def release_pending_turn(self, reservation: IMBindingReservation) -> bool:
        """Release exactly the reservation represented by ``reservation``."""

        tokens = self._pending_reservations.get(reservation.key)
        if tokens is None or reservation.token not in tokens:
            return False
        tokens.remove(reservation.token)
        if not tokens:
            self._pending_reservations.pop(reservation.key, None)
        return True

    def has_pending_turn(self, key: IMBindingKey) -> bool:
        return bool(self._pending_reservations.get(key))

    @asynccontextmanager
    async def try_mutation(self, key: IMBindingKey) -> AsyncIterator[None]:
        lock = self._lock_for(key)
        if lock.locked() or self.has_pending_turn(key):
            raise IMBindingBusyError("IM binding is busy")
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
