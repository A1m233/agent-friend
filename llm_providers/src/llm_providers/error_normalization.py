"""Provider-aware exception metadata extraction and project error mapping."""

from __future__ import annotations

import math
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import openai
from litellm.exceptions import (
    APIConnectionError as LiteLLMAPIConnectionError,
)
from litellm.exceptions import (
    ContentPolicyViolationError,
    ContextWindowExceededError,
    UnsupportedParamsError,
)
from litellm.exceptions import (
    Timeout as LiteLLMTimeout,
)
from litellm.litellm_core_utils.completion_timeout import CompletionTimeout

from .cancellable_stream import StreamIdleTimeoutExceeded
from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMContentPolicyError,
    LLMContextWindowError,
    LLMError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMStreamTimeoutError,
    LLMTransientProviderError,
    LLMUnsupportedParameterError,
)

_TRANSIENT_PROVIDER_ERROR_TYPES = frozenset({"engine_overloaded_error"})
_CONTEXT_WINDOW_ERROR_CODES = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "input_too_long",
        "prompt_too_long",
    }
)


@dataclass(frozen=True)
class _ProviderErrorMetadata:
    error_type: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None
    request_id: str | None = None


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_network_exception(error: Exception) -> bool:
    chain = tuple(_exception_chain(error))
    known_types = (
        openai.APIConnectionError,
        openai.APITimeoutError,
        LiteLLMAPIConnectionError,
        LiteLLMTimeout,
        CompletionTimeout,
        ssl.SSLError,
        ConnectionError,
        TimeoutError,
    )
    if any(isinstance(item, known_types) for item in chain):
        return True
    message = " | ".join(str(item) for item in chain)
    return any(
        marker in message
        for marker in (
            "SSL: UNEXPECTED_EOF_WHILE_READING",
            "EOF occurred in violation of protocol",
            "getaddrinfo failed",
        )
    )


def _parse_retry_after_seconds(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _header_text(headers: object, *names: str) -> str | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for name in names:
        try:
            value = getter(name)
        except Exception:
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _provider_error_metadata(error: Exception) -> _ProviderErrorMetadata:
    error_type: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None
    request_id: str | None = None
    for item in _exception_chain(error):
        item_status = getattr(item, "status_code", None)
        if status_code is None and isinstance(item_status, int):
            status_code = item_status
        body = getattr(item, "body", None)
        if isinstance(body, dict):
            payload = body.get("error", body)
            if isinstance(payload, dict):
                item_error_type = payload.get("type")
                if error_type is None and isinstance(item_error_type, str):
                    error_type = item_error_type.casefold()
        item_request_id = getattr(item, "request_id", None)
        if request_id is None and isinstance(item_request_id, str) and item_request_id:
            request_id = item_request_id
        response = getattr(item, "response", None)
        if response is None:
            continue
        response_status = getattr(response, "status_code", None)
        if status_code is None and isinstance(response_status, int):
            status_code = response_status
        headers = getattr(response, "headers", None)
        if request_id is None:
            request_id = _header_text(
                headers, "x-request-id", "x-moonshot-request-id", "request-id"
            )
        if retry_after_seconds is None:
            retry_after_seconds = _parse_retry_after_seconds(_header_text(headers, "retry-after"))
    return _ProviderErrorMetadata(
        error_type=error_type,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
        request_id=request_id,
    )


def _is_transient_provider_exception(error: Exception) -> bool:
    metadata = _provider_error_metadata(error)
    if metadata.status_code is not None and 500 <= metadata.status_code <= 599:
        return True
    return metadata.status_code == 429 and metadata.error_type in _TRANSIENT_PROVIDER_ERROR_TYPES


def _is_context_window_exception(error: Exception) -> bool:
    for item in _exception_chain(error):
        if isinstance(item, ContextWindowExceededError):
            return True
        code = getattr(item, "code", None)
        if isinstance(code, str) and code.casefold() in _CONTEXT_WINDOW_ERROR_CODES:
            return True
        body = getattr(item, "body", None)
        if isinstance(body, dict):
            payload = body.get("error", body)
            if isinstance(payload, dict):
                body_code = payload.get("code")
                if (
                    isinstance(body_code, str)
                    and body_code.casefold() in _CONTEXT_WINDOW_ERROR_CODES
                ):
                    return True
    return False


def _is_content_policy_exception(error: Exception) -> bool:
    if any(isinstance(item, ContentPolicyViolationError) for item in _exception_chain(error)):
        return True
    markers = (
        "content exists risk",
        "content_policy_violation",
        "content policy violation",
        "content_filter",
    )
    return any(
        marker in str(item).casefold() for item in _exception_chain(error) for marker in markers
    )


def map_provider_error(error: Exception, *, provider_id: str | None = None) -> LLMError:
    """Map SDK/provider exceptions without leaking transport types to callers."""

    chain = tuple(_exception_chain(error))
    metadata = _provider_error_metadata(error)

    # LiteLLM models this local validation error as an OpenAI BadRequestError.
    # Preserve its origin instead of implying that a provider rejected HTTP.
    if any(isinstance(item, UnsupportedParamsError) for item in chain):
        return LLMUnsupportedParameterError(str(error))

    def transient() -> LLMTransientProviderError:
        return LLMTransientProviderError(
            str(error),
            provider_error_type=metadata.error_type,
            status_code=metadata.status_code,
            retry_after_seconds=metadata.retry_after_seconds,
            provider_request_id=metadata.request_id,
        )

    error_type = (metadata.error_type or "").casefold()
    if provider_id == "openrouter":
        if _is_content_policy_exception(error) or error_type == "content_policy_violation":
            return LLMContentPolicyError(str(error))
        if metadata.status_code == 401 or error_type in {
            "authentication",
            "authentication_error",
            "invalid_api_key",
        }:
            return LLMAuthError(str(error))
        if metadata.status_code == 402 or error_type == "payment_required":
            return LLMPaymentRequiredError(str(error))
        if metadata.status_code == 403 or error_type in {
            "permission_denied",
            "guardrail_rejected",
        }:
            return LLMPermissionError(str(error))
        if metadata.status_code == 429 or error_type == "rate_limit_exceeded":
            return LLMRateLimitError(str(error))
        if (
            error_type
            in {
                "model_unavailable",
                "no_available_provider",
                "no_endpoint",
                "privacy_constraint",
                "routing_error",
            }
            or metadata.status_code == 404
        ):
            return LLMModelUnavailableError(
                str(error),
                availability_code=error_type or "no_endpoint",
                retryable=False,
                status_code=metadata.status_code,
                provider_request_id=metadata.request_id,
            )
        if error_type in {"provider_overloaded", "provider_unavailable"}:
            return transient()

    auth_error_types = {"authentication_error", "invalid_api_key", "permission_denied"}
    if any(isinstance(item, StreamIdleTimeoutExceeded) for item in chain):
        return LLMStreamTimeoutError(str(error))
    if (
        metadata.status_code in {401, 403}
        or error_type in auth_error_types
        or any(
            isinstance(item, (openai.AuthenticationError, openai.PermissionDeniedError))
            for item in chain
        )
    ):
        return LLMAuthError(str(error))
    if any(isinstance(item, openai.RateLimitError) for item in chain):
        return (
            transient()
            if _is_transient_provider_exception(error)
            else LLMRateLimitError(str(error))
        )
    if _is_network_exception(error):
        return LLMNetworkError(str(error))
    if _is_content_policy_exception(error):
        return LLMContentPolicyError(str(error))
    if _is_context_window_exception(error):
        return LLMContextWindowError(str(error))
    if any(isinstance(item, openai.BadRequestError) for item in chain):
        return LLMBadRequestError(str(error))
    if _is_transient_provider_exception(error):
        return transient()
    if isinstance(error, openai.APIError):
        return LLMProviderError(str(error))
    return LLMProviderError(f"未预料的错误: {error}")


__all__ = ["map_provider_error"]
