"""Provider-aware exception metadata extraction and project error mapping."""

from __future__ import annotations

import math
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256

import httpx
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
    ErrorEvidence,
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
    code: str | None = None
    message: str | None = None
    source: str = "unknown"
    error_type: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None
    request_id: str | None = None
    response_id: str | None = None


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
        httpx.TransportError,
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
    code: str | None = None
    message: str | None = None
    source = "unknown"
    error_type: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None
    request_id: str | None = None
    response_id: str | None = None
    for item in _exception_chain(error):
        item_response_id = getattr(item, "response_id", None)
        if response_id is None and isinstance(item_response_id, str):
            response_id = item_response_id
        item_source = getattr(item, "error_source", None)
        if item_source in {"stream_error", "failed_terminal", "sdk_validation", "protocol"}:
            source = item_source
        item_status = getattr(item, "status_code", None)
        if status_code is None and isinstance(item_status, int):
            status_code = item_status
        body = getattr(item, "body", None)
        if isinstance(body, dict):
            payload = body.get("error", body)
            if isinstance(payload, dict):
                body_code = payload.get("code")
                if code is None and isinstance(body_code, str):
                    code = body_code.casefold()
                body_message = payload.get("message")
                if message is None and isinstance(body_message, str):
                    message = body_message
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
        code=code,
        message=message,
        source=source if source != "unknown" else ("http" if status_code else "unknown"),
        error_type=error_type,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
        request_id=request_id,
        response_id=response_id,
    )


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


_ERROR_CODES = {
    "authentication": (LLMAuthError, "authentication"),
    "authentication_error": (LLMAuthError, "authentication"),
    "invalid_api_key": (LLMAuthError, "authentication"),
    "permission_denied": (LLMPermissionError, "permission"),
    "permission_error": (LLMPermissionError, "permission"),
    "guardrail_rejected": (LLMPermissionError, "permission"),
    "payment_required": (LLMPaymentRequiredError, "quota"),
    "insufficient_quota": (LLMPaymentRequiredError, "quota"),
    "billing_hard_limit_reached": (LLMPaymentRequiredError, "quota"),
    "rate_limit_exceeded": (LLMRateLimitError, "rate_limit"),
    "rate_limit_error": (LLMRateLimitError, "rate_limit"),
    "invalid_request_error": (LLMBadRequestError, "request"),
    "invalid_request": (LLMBadRequestError, "request"),
    "content_policy_violation": (LLMContentPolicyError, "content_policy"),
    "server_error": (LLMTransientProviderError, "service_unavailable"),
    "server_is_overloaded": (LLMTransientProviderError, "overload"),
    "engine_overloaded_error": (LLMTransientProviderError, "overload"),
    "provider_overloaded": (LLMTransientProviderError, "overload"),
    "provider_unavailable": (LLMTransientProviderError, "service_unavailable"),
    "service_unavailable_error": (LLMTransientProviderError, "service_unavailable"),
    **{code: (LLMContextWindowError, "context_window") for code in _CONTEXT_WINDOW_ERROR_CODES},
}
_ROUTE_CODES = {
    "model_unavailable",
    "no_available_provider",
    "no_endpoint",
    "privacy_constraint",
    "routing_error",
}


def _safe_token(value: str | None) -> str | None:
    if value is None:
        return None
    if value in _ERROR_CODES or value in _ROUTE_CODES:
        return value
    return "sha256:" + sha256(value.encode()).hexdigest()[:24]


def map_provider_error(error: Exception, *, provider_id: str | None = None) -> LLMError:
    """Normalize semantics once; never expose arbitrary provider text in an exception."""
    if isinstance(error, LLMError):
        return error
    chain = tuple(_exception_chain(error))
    metadata = _provider_error_metadata(error)
    code, kind = metadata.code, metadata.error_type
    source = metadata.source
    category = "unknown"
    cls: type[LLMError] = LLMProviderError
    if any(isinstance(item, UnsupportedParamsError) for item in chain):
        cls, category, source = LLMUnsupportedParameterError, "request", "sdk_validation"
    elif any(isinstance(item, StreamIdleTimeoutExceeded) for item in chain):
        cls, category, source = LLMStreamTimeoutError, "timeout", "transport"
    elif (
        metadata.status_code == 401
        or any(isinstance(item, openai.AuthenticationError) for item in chain)
        or _ERROR_CODES.get(code or "", (None, None))[0] is LLMAuthError
        or _ERROR_CODES.get(kind or "", (None, None))[0] is LLMAuthError
    ):
        # Providers can pair an authentication failure with a generic request code.
        # Preserve the actionable auth classification and the original evidence.
        cls, category = LLMAuthError, "authentication"
    elif code in _ERROR_CODES:
        cls, category = _ERROR_CODES[code]
    elif _is_context_window_exception(error):
        cls, category = LLMContextWindowError, "context_window"
    elif kind in _ERROR_CODES:
        cls, category = _ERROR_CODES[kind]
    elif _is_content_policy_exception(error):
        cls, category = LLMContentPolicyError, "content_policy"
    elif metadata.status_code == 402:
        cls, category = LLMPaymentRequiredError, "quota"
    elif metadata.status_code == 403 or isinstance(error, openai.PermissionDeniedError):
        cls, category = LLMPermissionError, "permission"
    elif metadata.status_code == 429 or isinstance(error, openai.RateLimitError):
        cls, category = LLMRateLimitError, "rate_limit"
    elif provider_id == "openrouter" and (
        code in _ROUTE_CODES or kind in _ROUTE_CODES or metadata.status_code == 404
    ):
        cls, category = LLMModelUnavailableError, "model_unavailable"
    elif _is_network_exception(error):
        cls, category, source = LLMNetworkError, "network", "transport"
    elif metadata.status_code == 408:
        cls, category = LLMTransientProviderError, "timeout"
    elif metadata.status_code is not None and 500 <= metadata.status_code <= 599:
        cls, category = LLMTransientProviderError, "service_unavailable"
    elif isinstance(error, openai.BadRequestError) or metadata.status_code in {400, 404, 422}:
        cls, category = LLMBadRequestError, "request"
    message = metadata.message or str(error)
    request_ref = (
        "sha256:" + sha256(metadata.request_id.encode()).hexdigest()[:24]
        if metadata.request_id
        else None
    )
    summary = f"Model call failed: {category}"
    if cls is LLMTransientProviderError:
        mapped: LLMError = LLMTransientProviderError(
            summary,
            provider_error_type=_safe_token(code or kind),
            status_code=metadata.status_code,
            retry_after_seconds=metadata.retry_after_seconds,
            provider_request_id=request_ref,
        )
    elif cls is LLMModelUnavailableError:
        mapped = LLMModelUnavailableError(
            summary,
            availability_code=_safe_token(code or kind) or "no_endpoint",
            status_code=metadata.status_code,
            provider_request_id=request_ref,
        )
    else:
        mapped = cls(summary)
    timeout = next((item for item in chain if isinstance(item, StreamIdleTimeoutExceeded)), None)
    sdk_timeout_stage = next(
        (
            stage
            for kind, stage in (
                (httpx.ConnectTimeout, "connect"),
                (httpx.PoolTimeout, "pool"),
                (httpx.WriteTimeout, "write"),
                (httpx.ReadTimeout, "unknown"),
                (openai.APITimeoutError, "unknown"),
                (LiteLLMTimeout, "unknown"),
            )
            for item in chain
            if isinstance(item, kind)
        ),
        None,
    )
    mapped.error_evidence = ErrorEvidence(
        source=source,
        timeout_stage=timeout.stage if timeout else sdk_timeout_stage,
        timeout_seconds=timeout.timeout_seconds if timeout else None,
        timeout_elapsed_seconds=timeout.elapsed_seconds if timeout else None,
        timeout_source=timeout.policy_source if timeout else None,
        timeout_last_activity=timeout.last_activity if timeout else None,
        timeout_stage_elapsed_seconds=timeout.stage_elapsed_seconds if timeout else None,
        category=category,
        code=_safe_token(code),
        error_type=_safe_token(kind),
        status_code=metadata.status_code,
        request_ref=request_ref,
        response_ref=(
            sha256(metadata.response_id.encode()).hexdigest()[:24] if metadata.response_id else None
        ),
        message_digest=sha256(message.encode()).hexdigest()[:24],
        message_length=len(message),
        retry_after_seconds=metadata.retry_after_seconds,
    )
    return mapped


__all__ = ["map_provider_error"]
