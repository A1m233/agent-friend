from __future__ import annotations

from pathlib import Path

import pytest
from memory_eval.__main__ import _provider_spec, _retrieval_code_fingerprint

from runtime_config import (
    DEEPSEEK_API_KEY,
    TAVILY_API_KEY,
    CredentialMissingError,
    credential_source_for_mode,
    optional_secret,
)


class FakeKeyring:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.values.get(name)


def test_retrieval_code_fingerprint_is_stable_sha256() -> None:
    first = _retrieval_code_fingerprint()
    second = _retrieval_code_fingerprint()

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_default_mode_uses_credential_center(tmp_path: Path) -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring({DEEPSEEK_API_KEY: "center-key"}),
        env={},
        settings_path=tmp_path / "settings.json",
    )

    spec = _provider_spec(None, source)

    assert spec.api_key == "center-key"


def test_provider_spec_allows_eval_timeout_override(tmp_path: Path) -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring({DEEPSEEK_API_KEY: "center-key"}),
        env={},
        settings_path=tmp_path / "settings.json",
    )

    spec = _provider_spec(None, source, timeout_seconds=300)

    assert spec.defaults["timeout"] == 300


def test_provider_spec_allows_zero_temperature_override(tmp_path: Path) -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring({DEEPSEEK_API_KEY: "center-key"}),
        env={},
        settings_path=tmp_path / "settings.json",
    )

    spec = _provider_spec(None, source, temperature=0)

    assert spec.defaults["temperature"] == 0


def test_legacy_dotenv_mode_reads_explicit_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("DEEPSEEK_API_KEY=legacy-key\n", encoding="utf-8")
    source = credential_source_for_mode(
        "legacy-dotenv",
        dotenv_path=dotenv,
        keyring_backend=FakeKeyring(),
        env={},
        settings_path=tmp_path / "settings.json",
    )

    spec = _provider_spec(None, source)

    assert spec.api_key == "legacy-key"


def test_missing_required_credential_has_unified_error(tmp_path: Path) -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring(),
        env={},
        settings_path=tmp_path / "settings.json",
    )

    with pytest.raises(CredentialMissingError, match="scripts/credentials/run\\.\\*"):
        _provider_spec(None, source)


def test_missing_optional_tavily_credential_stays_optional(tmp_path: Path) -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring({DEEPSEEK_API_KEY: "center-key"}),
        env={},
        settings_path=tmp_path / "settings.json",
    )

    assert optional_secret(source, TAVILY_API_KEY) is None
