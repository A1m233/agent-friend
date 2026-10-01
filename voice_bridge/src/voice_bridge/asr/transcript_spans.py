"""Preserve ASR time boundaries without guessing alignment after ITN formatting."""

from __future__ import annotations

import unicodedata
from typing import Any

from .types import AsrTranscriptSpan


def _formatting_only(text: str) -> bool:
    return all(char.isspace() or unicodedata.category(char).startswith("P") for char in text)


def extract_transcript_spans(payload: Any, text: str) -> tuple[AsrTranscriptSpan, ...]:
    """Prefer word boundaries, falling back to complete utterances.

    Word text may disagree with result.text (number normalization, punctuation).
    Only exact, ordered coverage is accepted; missing/invalid metadata stays absent.
    Source: https://www.volcengine.com/docs/6561/1354869
    """
    if not isinstance(payload, dict):
        return ()
    results = payload.get("result")
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        return ()
    utterances = [
        utterance
        for result in results
        if isinstance(result, dict) and isinstance(result.get("utterances"), list)
        for utterance in result["utterances"]
        if isinstance(utterance, dict)
    ]
    pieces: list[dict[str, Any]] = []
    for utterance in utterances:
        words = utterance.get("words")
        utterance_text = utterance.get("text")
        if isinstance(words, list) and isinstance(utterance_text, str):
            aligned = _align(words, utterance_text)
            if aligned:
                pieces.extend(
                    {"text": s.text, "start_time": s.start_ms, "end_time": s.end_ms}
                    for s in aligned
                )
                continue
        pieces.append(utterance)
    return _align(pieces, text)


def _align(pieces: list[Any], text: str) -> tuple[AsrTranscriptSpan, ...]:
    spans: list[AsrTranscriptSpan] = []
    offset = 0
    previous_end = 0
    for piece in pieces:
        if not isinstance(piece, dict):
            return ()
        value = piece.get("text")
        start, end = piece.get("start_time"), piece.get("end_time")
        if (
            not isinstance(value, str)
            or not value
            or type(start) is not int
            or type(end) is not int
            or start < previous_end
            or end < start
        ):
            return ()
        position = text.find(value, offset)
        if position < 0 or not _formatting_only(text[offset:position]):
            return ()
        if spans:
            prev = spans[-1]
            spans[-1] = AsrTranscriptSpan(
                prev.text + text[offset:position], prev.start_ms, prev.end_ms
            )
            spans.append(AsrTranscriptSpan(value, start, end))
        else:
            spans.append(AsrTranscriptSpan(text[:position] + value, start, end))
        offset = position + len(value)
        previous_end = end
    if not spans or not _formatting_only(text[offset:]):
        return ()
    prev = spans[-1]
    spans[-1] = AsrTranscriptSpan(prev.text + text[offset:], prev.start_ms, prev.end_ms)
    return tuple(spans)
