"""Keep SDK validation distinct from a provider's HTTP rejection."""

import httpx
import openai
import pytest
from litellm.exceptions import UnsupportedParamsError
from llm_providers.error_normalization import map_provider_error

from llm_providers import LLMBadRequestError, LLMUnsupportedParameterError


@pytest.mark.parametrize("wrapped", [False, True])
def test_sdk_parameter_rejection_keeps_local_origin(wrapped: bool) -> None:
    error = UnsupportedParamsError(
        status_code=500,
        message="openai does not support parameters: ['reasoning_effort']",
    )
    outer: Exception = error
    if wrapped:
        outer = RuntimeError("completion failed")
        outer.__cause__ = error

    result = map_provider_error(outer, provider_id="kimi")

    assert isinstance(result, LLMUnsupportedParameterError)
    assert isinstance(result, LLMBadRequestError)
    assert result.error_source == "sdk_validation"
    assert result.reason_code == "unsupported_model_parameter"


def test_provider_bad_request_is_not_misreported_as_local_validation() -> None:
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    )
    error = openai.BadRequestError("invalid request", response=response, body=None)

    result = map_provider_error(error, provider_id="kimi")

    assert isinstance(result, LLMBadRequestError)
    assert not isinstance(result, LLMUnsupportedParameterError)
