"""AG-UI request decoder tests."""

from __future__ import annotations

import pytest
from ag_ui.core import RunAgentInput
from agent_bridge.protocols.ag_ui.decoders import DecodeError, decode_run_agent_input


def _payload(model: object | None = None) -> RunAgentInput:
    forwarded_props = {} if model is None else {"model": model}
    return RunAgentInput.model_validate(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "u-1", "role": "user", "content": "hi"}],
            "tools": [],
            "context": [],
            "forwarded_props": forwarded_props,
            "state": {},
        }
    )


def test_decode_accepts_allowed_desktop_requested_model() -> None:
    decoded = decode_run_agent_input(_payload("deepseek/deepseek-flash"))

    assert decoded.requested_model == "deepseek/deepseek-flash"


def test_decode_accepts_kimi_desktop_requested_model() -> None:
    decoded = decode_run_agent_input(_payload("kimi/kimi-k2.6"))

    assert decoded.requested_model == "kimi/kimi-k2.6"


def test_decode_accepts_provider_aware_requested_model() -> None:
    decoded = decode_run_agent_input(
        _payload({"provider": "kimi", "model": "kimi/kimi-k2.7-code-highspeed"})
    )

    assert decoded.requested_model == "kimi/kimi-k2.7-code-highspeed"


def test_decode_rejects_unsupported_desktop_requested_model() -> None:
    with pytest.raises(DecodeError, match="不支持的桌面模型"):
        decode_run_agent_input(_payload("openai/gpt-4.1"))


def test_decode_ignores_missing_desktop_requested_model() -> None:
    decoded = decode_run_agent_input(_payload())

    assert decoded.requested_model is None


def test_decode_accepts_risk_acknowledged_initial_session_auto() -> None:
    payload = _payload()
    payload.forwarded_props = {
        "initialApprovalMode": {
            "mode": "session_auto",
            "riskAcknowledged": True,
        }
    }

    decoded = decode_run_agent_input(payload)

    assert decoded.initial_approval_mode == "session_auto"


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "session_auto"},
        {"mode": "per_request", "riskAcknowledged": True},
        "session_auto",
    ],
)
def test_decode_rejects_invalid_initial_approval_mode(value: object) -> None:
    payload = _payload()
    payload.forwarded_props = {"initialApprovalMode": value}

    with pytest.raises(DecodeError, match=r"initialApprovalMode|确认风险"):
        decode_run_agent_input(payload)


def test_decode_rejects_empty_user_content() -> None:
    payload = RunAgentInput.model_validate(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "u-1", "role": "user", "content": "  "}],
            "tools": [],
            "context": [],
            "forwarded_props": {},
            "state": {},
        }
    )

    with pytest.raises(DecodeError, match="content 不能为空"):
        decode_run_agent_input(payload)


def test_decode_accepts_quote_only_user_message() -> None:
    payload = RunAgentInput.model_validate(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [{"id": "u-quote", "role": "user", "content": ""}],
            "tools": [],
            "context": [],
            "forwarded_props": {
                "quotedTextReferences": [
                    {
                        "reference_id": "ref-1",
                        "quoted_text": "这段话",
                        "source_role": "assistant",
                        "availability": "available",
                        "source": {
                            "kind": "session_message",
                            "message_event_uuid": "assistant-1",
                            "text_block_id": "assistant-block-1",
                            "external_source_key": None,
                        },
                        "locator": {
                            "kind": "text_range",
                            "coordinate_space": "rendered_text_utf16_v1",
                            "start": 0,
                            "end": 4,
                        },
                    }
                ]
            },
            "state": {},
        }
    )

    decoded = decode_run_agent_input(payload)

    assert decoded.new_user_input == ""
    assert decoded.references[0].quoted_text == "这段话"
    assert decoded.references[0].source.message_event_uuid == "assistant-1"


def test_decode_rejects_malformed_quote_instead_of_dropping_it() -> None:
    payload = _payload()
    payload.forwarded_props = {
        "quotedTextReferences": [
            {
                "reference_id": "bad",
                "quoted_text": "x",
                "source_role": "assistant",
                "availability": "available",
                "source": {
                    "kind": "external_message",
                    "external_source_key": "opaque",
                },
                "locator": {
                    "kind": "text_range",
                    "coordinate_space": "rendered_text_utf16_v1",
                    "start": 0,
                    "end": 1,
                },
            }
        ]
    }

    with pytest.raises(DecodeError, match="quotedTextReferences"):
        decode_run_agent_input(payload)


def test_decode_accepts_standard_attachment_only_content() -> None:
    payload = RunAgentInput.model_validate(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [
                {
                    "id": "u-attachment",
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "value": "/v1/attachments/drafts/abcdefghijklmnop/content",
                                "mime_type": "image/png",
                            },
                        }
                    ],
                }
            ],
            "tools": [],
            "context": [],
            "forwarded_props": {"model": "kimi/kimi-k2.6"},
            "state": {},
        }
    )

    decoded = decode_run_agent_input(payload)

    assert decoded.new_user_input == ""
    assert decoded.user_message_id == "u-attachment"
    assert decoded.draft_attachments[0].draft_id == "abcdefghijklmnop"
    assert decoded.draft_attachments[0].kind == "image"


def test_decode_rejects_external_attachment_url() -> None:
    payload = RunAgentInput.model_validate(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "messages": [
                {
                    "id": "u-attachment",
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "url",
                                "value": "https://example.com/report.pdf",
                            },
                        }
                    ],
                }
            ],
            "tools": [],
            "context": [],
            "forwarded_props": {},
            "state": {},
        }
    )

    with pytest.raises(DecodeError, match="本机"):
        decode_run_agent_input(payload)
