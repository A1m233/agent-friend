from __future__ import annotations

from types import SimpleNamespace

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
