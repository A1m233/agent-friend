"""Small persistence value types shared without importing the store implementation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionLoadDiagnostics:
    """Content-free facts about recoverable malformed JSONL records."""

    parse_error_count: int = 0
    malformed_bytes: int = 0
    last_error_line: int | None = None
    unhandled_tail_corruption: bool = False


__all__ = ["SessionLoadDiagnostics"]
