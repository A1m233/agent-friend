"""Latency helper tests."""

from __future__ import annotations

from voice_bridge.latency import SseTextObserver


def test_sse_observer_ignores_role_and_detects_first_text() -> None:
    observer = SseTextObserver()

    assert (
        observer.feed(b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n')
        is None
    )
    assert observer.feed(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n') == "hi"
    assert observer.feed(b'data: {"choices":[{"delta":{"content":"later"}}]}\n\n') is None


def test_sse_observer_handles_split_frames() -> None:
    observer = SseTextObserver()

    assert observer.feed(b'data: {"choices":[{"delta":{"content":"hel') is None
    assert observer.feed(b'lo"}}]}\n\n') == "hello"


def test_sse_observer_ignores_done_and_usage_only() -> None:
    observer = SseTextObserver()

    assert observer.feed(b"data: [DONE]\n\n") is None
    assert observer.feed(b'data: {"choices":[],"usage":{"total_tokens":1}}\n\n') is None
    assert observer.first_text is None
