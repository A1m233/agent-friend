"""Volc callback payload decoding tests."""

from __future__ import annotations

import base64
import json
from typing import Any

from voice_bridge.callbacks import decode_volc_callback_payload


def _encoded(
    prefix: str, payload: dict[str, Any], *, header_tail: bytes = b"\x00\x00\x00"
) -> dict[str, Any]:
    body = prefix.encode("ascii") + header_tail + json.dumps(payload).encode("utf-8")
    return {
        "message": base64.b64encode(body).decode("ascii"),
        "binary": True,
        "signature": "agent-friend-smoke",
    }


def test_decode_state_callback_binary_payload() -> None:
    decoded = decode_volc_callback_payload(
        _encoded(
            "conv",
            {
                "EventTime": 1783187222843,
                "RoundID": 2,
                "Stage": {"Code": 3, "Description": "answering"},
                "TaskId": "call-1",
                "UserID": "user-1",
            },
        )
    )

    assert decoded.payload_type == "conv"
    assert decoded.stage == "answering"
    assert decoded.stage_code == 3
    assert decoded.round_id == 2
    assert decoded.event_time_ms == 1783187222843
    assert decoded.task_id == "call-1"
    assert decoded.user_id == "user-1"
    assert decoded.text_len is None


def test_decode_subtitle_callback_binary_payload() -> None:
    decoded = decode_volc_callback_payload(
        _encoded(
            "subv",
            {
                "type": "subtitle",
                "data": [
                    {
                        "definite": True,
                        "roundId": 1,
                        "sequence": 17,
                        "text": "hello",
                        "timestamp": 1783187224434,
                        "userId": "bot-1",
                    }
                ],
            },
        )
    )

    assert decoded.payload_type == "subv"
    assert decoded.stage == "subtitle"
    assert decoded.round_id == 1
    assert decoded.event_time_ms == 1783187224434
    assert decoded.user_id == "bot-1"
    assert decoded.definite is True
    assert decoded.sequence == 17
    assert decoded.text == "hello"
    assert decoded.text_len == 5
    assert decoded.item_count == 1


def test_decode_binary_payload_type_ignores_non_ascii_header_bytes() -> None:
    decoded = decode_volc_callback_payload(
        _encoded(
            "conv",
            {
                "EventTime": 1783187222843,
                "RoundID": 1,
                "Stage": {"Code": 1, "Description": "listening"},
                "TaskId": "call-1",
                "UserID": "user-1",
            },
            header_tail=b"\xc3\xa0\x00",
        )
    )

    assert decoded.payload_type == "conv"


def test_decode_plain_callback_payload() -> None:
    decoded = decode_volc_callback_payload(
        {
            "stage": "thinking",
            "roundId": "3",
            "timestamp": "1783187224488",
            "userId": "user-1",
        }
    )

    assert decoded.stage == "thinking"
    assert decoded.round_id == 3
    assert decoded.event_time_ms == 1783187224488
    assert decoded.user_id == "user-1"


def test_unknown_callback_payload_returns_empty_result() -> None:
    decoded = decode_volc_callback_payload({"message": "not-base64"})

    assert decoded.stage == ""
    assert decoded.payload_type == ""
