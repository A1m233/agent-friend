"""Agent runtime coordination errors."""

from __future__ import annotations


class SessionBusyError(RuntimeError):
    """The session already has an active, queued, or mutating operation."""


__all__ = ["SessionBusyError"]
