from __future__ import annotations

import pytest
from agent.context.errors import ContextCompactionUnavailableError
from agent_bridge.errors import map_exception

from llm_providers import (
    LLMAuthError,
    LLMContextWindowError,
    LLMError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMProviderError,
    LLMTransientProviderError,
    LLMUnsupportedParameterError,
)


@pytest.mark.parametrize(
    "error,code,recoverable",
    [
        (LLMProviderError("private"), "model_provider_error", False),
        (LLMAuthError("private"), "auth_failed", False),
        (LLMPermissionError("private"), "permission_denied", False),
        (LLMPaymentRequiredError("private"), "payment_required", False),
        (LLMContextWindowError("private"), "context_window_exceeded", False),
        (LLMUnsupportedParameterError("private"), "unsupported_model_parameter", False),
        (
            LLMTransientProviderError("private", provider_error_type="server_is_overloaded"),
            "upstream_transient",
            True,
        ),
    ],
)
def test_model_failure_presentation(error: LLMError, code: str, recoverable: bool) -> None:
    result = map_exception(error)
    assert result.code == code and result.recoverable is recoverable
    assert "private" not in result.message
    wrapped = ContextCompactionUnavailableError("private")
    wrapped.__cause__ = error
    projection = map_exception(wrapped)
    assert projection.code == "context_compaction_unavailable"
    assert projection.recoverable is recoverable
    assert result.message in projection.message and "已保留原历史" in projection.message


def test_local_request_and_protocol_errors_keep_their_origin() -> None:
    from llm_providers.responses.request import _invalid
    from llm_providers.responses.stream import _protocol

    assert map_exception(_invalid("unsupported request settings")).code == "model_request_invalid"
    assert map_exception(_protocol("expected an object")).code == "model_protocol_error"
