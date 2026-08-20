"""Low-side-effect provider credential checks used by desktop settings."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import openai

from .catalog import (
    DEEPSEEK_PROVIDER_ID,
    KIMI_API_BASE,
    KIMI_PROVIDER_ID,
    OPENROUTER_PROVIDER_ID,
)

CredentialValidationKind = Literal["valid", "invalid", "temporarily_unavailable"]


@dataclass(frozen=True)
class CredentialValidationResult:
    kind: CredentialValidationKind
    safe_code: str


_PROVIDER_BASE_URLS = {
    DEEPSEEK_PROVIDER_ID: "https://api.deepseek.com",
    KIMI_PROVIDER_ID: KIMI_API_BASE,
}
_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


def _status_code(exc: BaseException) -> int | None:
    for item in (exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        status = getattr(item, "status_code", None)
        if isinstance(status, int):
            return status
        response = getattr(item, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def classify_credential_validation_error(exc: Exception) -> CredentialValidationResult:
    """Classify only explicit auth evidence as an invalid credential."""

    status = _status_code(exc)
    if status in {401, 403} or isinstance(
        exc,
        (openai.AuthenticationError, openai.PermissionDeniedError),
    ):
        return CredentialValidationResult("invalid", "credential_rejected")
    if status == 429:
        return CredentialValidationResult("temporarily_unavailable", "provider_rate_limited")
    if status is not None and 500 <= status <= 599:
        return CredentialValidationResult("temporarily_unavailable", "provider_unavailable")
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return CredentialValidationResult("temporarily_unavailable", "provider_unreachable")
    return CredentialValidationResult("temporarily_unavailable", "validation_unavailable")


def validate_provider_credential(
    provider_id: str,
    api_key: str,
    *,
    client_factory: Callable[..., Any] = openai.OpenAI,
    http_client_factory: Callable[..., Any] = httpx.Client,
) -> CredentialValidationResult:
    """Call the provider's model-list endpoint without creating a completion."""

    if provider_id == OPENROUTER_PROVIDER_ID:
        client: Any | None = None
        try:
            client = http_client_factory(timeout=8.0)
            response = client.get(
                _OPENROUTER_KEY_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            status = int(response.status_code)
        except (httpx.TimeoutException, httpx.RequestError):
            return CredentialValidationResult("temporarily_unavailable", "provider_unreachable")
        except Exception:
            return CredentialValidationResult("temporarily_unavailable", "validation_unavailable")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()
        if status == 200:
            return CredentialValidationResult("valid", "credential_accepted")
        if status == 401:
            return CredentialValidationResult("invalid", "credential_rejected")
        if status == 429:
            return CredentialValidationResult("temporarily_unavailable", "provider_rate_limited")
        if 500 <= status <= 599:
            return CredentialValidationResult("temporarily_unavailable", "provider_unavailable")
        return CredentialValidationResult("temporarily_unavailable", "validation_unavailable")

    base_url = _PROVIDER_BASE_URLS.get(provider_id)
    if base_url is None:
        return CredentialValidationResult("temporarily_unavailable", "validation_unsupported")
    try:
        client = client_factory(
            api_key=api_key,
            base_url=base_url,
            timeout=8.0,
            max_retries=0,
        )
        client.models.list()
    except Exception as exc:
        return classify_credential_validation_error(exc)
    return CredentialValidationResult("valid", "credential_accepted")
