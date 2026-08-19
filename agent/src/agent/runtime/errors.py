"""Agent runtime coordination errors."""

from __future__ import annotations


class SessionBusyError(RuntimeError):
    """The session already has an active, queued, or mutating operation."""


def run_failure_reason_code(exc: BaseException, *, default: str) -> str:
    """Return a stable runtime reason code without exposing exception text."""

    reason_code = getattr(exc, "reason_code", None)
    return reason_code if isinstance(reason_code, str) and reason_code else default


__all__ = ["SessionBusyError", "run_failure_reason_code"]
