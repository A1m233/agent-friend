from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from llm_providers.credential_validation import (
    classify_credential_validation_error,
    validate_provider_credential,
)


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider rejected request")
        self.status_code = status_code


def test_models_list_success_is_valid_without_completion() -> None:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(models=SimpleNamespace(list=lambda: ["model"]))

    result = validate_provider_credential("deepseek", "fixture-secret", client_factory=factory)

    assert result.kind == "valid"
    assert result.safe_code == "credential_accepted"
    assert calls == [
        {
            "api_key": "fixture-secret",
            "base_url": "https://api.deepseek.com",
            "timeout": 8.0,
            "max_retries": 0,
        }
    ]


def test_explicit_401_and_403_are_invalid() -> None:
    assert classify_credential_validation_error(_StatusError(401)).kind == "invalid"
    assert classify_credential_validation_error(_StatusError(403)).kind == "invalid"


def test_rate_limit_server_and_ambiguous_errors_are_not_invalid() -> None:
    for error in (_StatusError(429), _StatusError(503), _StatusError(400)):
        assert classify_credential_validation_error(error).kind == "temporarily_unavailable"


def test_unsupported_provider_never_constructs_client() -> None:
    result = validate_provider_credential(
        "tavily",
        "fixture-secret",
        client_factory=lambda **_: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    assert result.safe_code == "validation_unsupported"


@pytest.mark.parametrize(
    ("status", "kind", "safe_code"),
    [
        (200, "valid", "credential_accepted"),
        (401, "invalid", "credential_rejected"),
        (403, "temporarily_unavailable", "validation_unavailable"),
        (429, "temporarily_unavailable", "provider_rate_limited"),
        (503, "temporarily_unavailable", "provider_unavailable"),
    ],
)
def test_openrouter_uses_key_endpoint_without_reading_account_metadata(
    status: int,
    kind: str,
    safe_code: str,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    class Client:
        def get(self, url: str, *, headers: dict[str, str]) -> object:
            calls.append((url, headers))
            return SimpleNamespace(
                status_code=status,
                json=lambda: (_ for _ in ()).throw(AssertionError("must not read metadata")),
            )

        def close(self) -> None:
            pass

    result = validate_provider_credential(
        "openrouter",
        "fixture-secret",
        http_client_factory=lambda **_: Client(),
    )

    assert result.kind == kind
    assert result.safe_code == safe_code
    assert calls == [
        (
            "https://openrouter.ai/api/v1/key",
            {"Authorization": "Bearer fixture-secret"},
        )
    ]


def test_openrouter_network_failure_is_not_invalid() -> None:
    def unavailable(**_: object) -> object:
        raise httpx.ConnectError("offline")

    result = validate_provider_credential(
        "openrouter",
        "fixture-secret",
        http_client_factory=unavailable,
    )

    assert result.kind == "temporarily_unavailable"
    assert result.safe_code == "provider_unreachable"
