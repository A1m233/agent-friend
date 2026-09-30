"""Small persistence value types shared without importing the store implementation."""

from __future__ import annotations

from dataclasses import dataclass

SourceVersion = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class SessionLoadDiagnostics:
    """Content-free facts about recoverable malformed JSONL records."""

    parse_error_count: int = 0
    malformed_bytes: int = 0
    last_error_line: int | None = None
    unhandled_tail_corruption: bool = False


@dataclass
class SessionReadState:
    """Metadata published as a streaming read reaches its validated end."""

    source_version: SourceVersion | None = None
    diagnostics: SessionLoadDiagnostics = SessionLoadDiagnostics()
    event_count: int = 0


__all__ = ["SessionLoadDiagnostics"]
