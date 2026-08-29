"""Trusted host time boundary shared by model context and tool actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class TimeProvider(Protocol):
    """Return the host's current timezone-aware local time."""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class SystemTimeProvider:
    """Production clock backed by the host operating system."""

    def now(self) -> datetime:
        return datetime.now().astimezone()


@dataclass(frozen=True)
class CallableTimeProvider:
    """Compatibility adapter for existing injected ``() -> datetime`` clocks."""

    clock: Callable[[], datetime]

    def now(self) -> datetime:
        return self.clock()


def read_host_time(provider: TimeProvider) -> tuple[datetime, str]:
    """Read and validate one trusted action-time snapshot."""

    current = provider.now()
    if current.tzinfo is None:
        raise ValueError("trusted now must be timezone-aware")
    zone = getattr(current.tzinfo, "key", None) or _offset_text(current)
    return current, str(zone)


def _offset_text(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return "UTC"
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


__all__ = [
    "CallableTimeProvider",
    "SystemTimeProvider",
    "TimeProvider",
    "read_host_time",
]
