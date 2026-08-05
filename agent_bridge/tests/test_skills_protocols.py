from __future__ import annotations

from typing import Any

import pytest
from ag_ui.core import RunAgentInput
from agent_bridge.protocols.ag_ui.decoders import (
    DecodeError as AgUiDecodeError,
)
from agent_bridge.protocols.ag_ui.decoders import decode_run_agent_input
from agent_bridge.protocols.openai.decoders import (
    DecodeError as OpenAIDecodeError,
)
from agent_bridge.protocols.openai.decoders import decode_chat_completion_request


def _ag_ui_payload(skills: object | None) -> RunAgentInput:
    forwarded_props = {} if skills is None else {"skills": skills}
    return RunAgentInput.model_validate(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "user-1", "role": "user", "content": "$named-skill"}],
            "tools": [],
            "context": [],
            "forwarded_props": forwarded_props,
            "state": {},
        }
    )


def _openai_payload(skills: object | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": "$named-skill"}],
    }
    if skills is not None:
        payload["agent_friend"] = {"skills": skills}
    return payload


@pytest.mark.parametrize(
    "decode,payload",
    [
        (decode_run_agent_input, _ag_ui_payload(None)),
        (decode_chat_completion_request, _openai_payload(None)),
    ],
)
def test_plain_skill_like_query_does_not_activate(decode: Any, payload: Any) -> None:
    assert decode(payload).skills == ()


def test_both_protocols_decode_the_same_selection_and_dedupe() -> None:
    version = "a" * 64
    wire = [
        {"id": "skill_123", "expected_version": version},
        {"id": "skill_123", "expected_version": "b" * 64},
    ]

    ag_ui = decode_run_agent_input(_ag_ui_payload(wire)).skills
    openai = decode_chat_completion_request(_openai_payload(wire)).skills

    assert ag_ui == openai
    assert len(ag_ui) == 1
    assert ag_ui[0].id == "skill_123"
    assert ag_ui[0].expected_version == version


def test_ag_ui_rejects_name_or_path_fallback_shape() -> None:
    with pytest.raises(AgUiDecodeError, match="未知字段"):
        decode_run_agent_input(_ag_ui_payload([{"id": "skill_123", "name": "named"}]))


def test_openai_rejects_invalid_expected_version() -> None:
    with pytest.raises(OpenAIDecodeError, match="完整 SHA-256"):
        decode_chat_completion_request(
            _openai_payload([{"id": "skill_123", "expected_version": "latest"}])
        )
