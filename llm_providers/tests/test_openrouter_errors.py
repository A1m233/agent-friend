from __future__ import annotations

import pytest
from llm_providers.error_normalization import map_provider_error

from llm_providers import (
    LLMAuthError,
    LLMContentPolicyError,
    LLMModelUnavailableError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMRateLimitError,
    LLMTransientProviderError,
)


class _ProviderError(Exception):
    def __init__(self, status_code: int, error_type: str | None = None) -> None:
        super().__init__("provider rejected request")
        self.status_code = status_code
        self.body = {"error": {"type": error_type}} if error_type else None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_ProviderError(401, "authentication"), LLMAuthError),
        (_ProviderError(402, "payment_required"), LLMPaymentRequiredError),
        (_ProviderError(403, "permission_denied"), LLMPermissionError),
        (_ProviderError(403, "content_policy_violation"), LLMContentPolicyError),
        (_ProviderError(429, "rate_limit_exceeded"), LLMRateLimitError),
        (_ProviderError(503, "provider_overloaded"), LLMTransientProviderError),
        (_ProviderError(404, "no_available_provider"), LLMModelUnavailableError),
    ],
)
def test_openrouter_error_categories_do_not_collapse_to_auth(
    error: Exception,
    expected: type[Exception],
) -> None:
    assert isinstance(map_provider_error(error, provider_id="openrouter"), expected)


def test_direct_provider_403_keeps_existing_auth_semantics() -> None:
    assert isinstance(map_provider_error(_ProviderError(403), provider_id="deepseek"), LLMAuthError)
