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
    decoded = decode_run_agent_input(_payload("deepseek/deepseek-v4-flash"))

    assert decoded.requested_model == "deepseek/deepseek-v4-flash"


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
