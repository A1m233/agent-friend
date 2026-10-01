"""Timed ASR metadata must describe the exact formatted transcript."""

import pytest
from voice_bridge.asr.transcript_spans import extract_transcript_spans
from voice_bridge.asr.types import AsrTranscriptSpan


def test_words_preserve_punctuation_whitespace_and_unicode() -> None:
    payload = {
        "result": {
            "utterances": [
                {
                    "text": "你好， world！",
                    "start_time": 0,
                    "end_time": 900,
                    "words": [
                        {"text": "你好", "start_time": 0, "end_time": 400},
                        {"text": "world", "start_time": 500, "end_time": 900},
                    ],
                }
            ]
        }
    }
    assert extract_transcript_spans(payload, "你好， world！") == (
        AsrTranscriptSpan("你好， ", 0, 400),
        AsrTranscriptSpan("world！", 500, 900),
    )


def test_falls_back_to_utterance_when_normalized_words_do_not_align() -> None:
    payload = {
        "result": [
            {
                "utterances": [
                    {
                        "text": "100个",
                        "start_time": 0,
                        "end_time": 900,
                        "words": [{"text": "一百个", "start_time": 0, "end_time": 900}],
                    }
                ]
            }
        ]
    }
    assert extract_transcript_spans(payload, "100个") == (AsrTranscriptSpan("100个", 0, 900),)


@pytest.mark.parametrize(
    "utterance",
    [
        {"text": "缺字", "start_time": 0, "end_time": 10},
        {"text": "你好", "start_time": -1, "end_time": 10},
        {"text": "你好", "start_time": 10, "end_time": 0},
        {"text": "你好", "start_time": True, "end_time": 10},
        {"text": "你好"},
    ],
)
def test_does_not_invent_missing_or_invalid_boundaries(utterance: object) -> None:
    assert extract_transcript_spans({"result": {"utterances": [utterance]}}, "你好") == ()


def test_rejects_overlapping_times() -> None:
    payload = {
        "result": {
            "utterances": [
                {"text": "你", "start_time": 0, "end_time": 100},
                {"text": "好", "start_time": 90, "end_time": 200},
            ]
        }
    }
    assert extract_transcript_spans(payload, "你好") == ()
