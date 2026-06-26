"""Latency tracing helpers for the voice call path."""

from __future__ import annotations

import json
import logging
import time
from typing import Any


def monotonic_ms() -> int:
    """Return a monotonic timestamp in milliseconds."""
    return int(time.perf_counter() * 1000)


def log_latency(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Log a compact key-value latency event.

    The format intentionally stays plain text so it remains easy to grep in the
    existing log files without introducing a second sink.
    """
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_format_value(value)}")
    logger.info("voice_latency %s", " ".join(parts))


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text or any(ch.isspace() for ch in text):
        return json.dumps(text, ensure_ascii=False)
    return text


class SseTextObserver:
    """Observe an SSE byte stream and detect the first real text delta.

    This parser is deliberately best-effort. It never owns the stream, never
    changes chunk boundaries, and parse failures are exposed as counters so the
    proxy can continue forwarding the original bytes.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self.first_text: str | None = None
        self.parse_failures = 0

    def feed(self, chunk: bytes) -> str | None:
        """Feed raw SSE bytes and return the first content text when found."""
        if self.first_text is not None:
            return None

        self._buffer += chunk.decode("utf-8", errors="replace")
        self._buffer = self._buffer.replace("\r\n", "\n")

        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            text = self._first_text_from_frame(frame)
            if text:
                self.first_text = text
                return text
        return None

    def _first_text_from_frame(self, frame: str) -> str | None:
        data_lines: list[str] = []
        for raw_line in frame.split("\n"):
            line = raw_line.strip()
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())

        if not data_lines:
            return None
        data = "\n".join(data_lines)
        if not data or data == "[DONE]":
            return None

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            self.parse_failures += 1
            return None

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return None
        delta = first_choice.get("delta")
        if not isinstance(delta, dict):
            return None
        content = delta.get("content")
        if isinstance(content, str) and content:
            return content
        return None
