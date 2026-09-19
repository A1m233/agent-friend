from __future__ import annotations

import json
import traceback
from dataclasses import asdict

import pytest
from llm_providers.error_normalization import map_provider_error
from llm_providers.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMContextWindowError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTransientProviderError,
)
from llm_providers.responses.stream import _ResponseFailure


class HttpFailure(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__("private body sk-secret https://private/?key=secret")
        self.status_code = status
        self.body = {"error": {"code": code, "type": "invalid_request_error", "message": str(self)}}
        self.request_id = "private request identifier"


@pytest.mark.parametrize(
    "code,status,expected",
    [
        ("invalid_api_key", 401, LLMAuthError),
        ("permission_denied", 403, LLMPermissionError),
        ("insufficient_quota", 429, LLMPaymentRequiredError),
        ("rate_limit_exceeded", 429, LLMRateLimitError),
        ("context_length_exceeded", 400, LLMContextWindowError),
        ("invalid_request_error", 400, LLMBadRequestError),
        ("server_is_overloaded", 503, LLMTransientProviderError),
        ("engine_overloaded_error", 429, LLMTransientProviderError),
    ],
)
def test_equivalent_error_carriers(code: str, status: int, expected: type[Exception]) -> None:
    http = HttpFailure(code, status)
    event = _ResponseFailure({"type": "error", "code": code, "message": str(http)}, "resp")
    terminal = _ResponseFailure({"code": code, "message": str(http)}, "resp")
    for original in (http, event, terminal):
        mapped = map_provider_error(original)
        assert isinstance(mapped, expected)
        assert mapped.error_evidence is not None
        safe = json.dumps(asdict(mapped.error_evidence)) + str(mapped)
        assert "private" not in safe and "sk-secret" not in safe
        assert mapped.error_evidence.status_code == (status if original is http else None)
    event_evidence = map_provider_error(event).error_evidence
    terminal_evidence = map_provider_error(terminal).error_evidence
    assert event_evidence is not None and event_evidence.source == "stream_error"
    assert terminal_evidence is not None and terminal_evidence.source == "failed_terminal"


def test_unknown_error_preserves_digest_not_untrusted_text() -> None:
    error = _ResponseFailure(
        {"type": "error", "code": "sk-secret", "message": "private" * 10000}, None
    )
    mapped = map_provider_error(error)
    assert type(mapped) is LLMProviderError
    assert mapped.error_evidence is not None
    assert mapped.error_evidence.message_length == 70000
    assert mapped.error_evidence.code is not None
    assert mapped.error_evidence.code.startswith("sha256:")
    assert len(json.dumps(asdict(mapped.error_evidence))) < 1000
    try:
        try:
            raise error
        except Exception as raw:
            raise map_provider_error(raw) from None
    except LLMProviderError:
        assert "sk-secret" not in traceback.format_exc()
