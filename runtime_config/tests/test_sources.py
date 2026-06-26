"""runtime_config credential source tests."""

from __future__ import annotations

from pathlib import Path

from runtime_config import (
    DEEPSEEK_API_KEY,
    VOICE_BRIDGE_PUBLIC_URL,
    KeyringSource,
    LegacyDotenvSource,
    credential_source_for_mode,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.values.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.values[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.values.pop((service, name), None)


def test_process_env_wins_over_keyring_and_dotenv(tmp_path: Path) -> None:
    fake = FakeKeyring()
    fake.set_password("agent-friend", DEEPSEEK_API_KEY, "from-keyring")
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{DEEPSEEK_API_KEY}=from-dotenv\n", encoding="utf-8")

    source = credential_source_for_mode(
        "legacy-dotenv",
        dotenv_path=dotenv,
        keyring_backend=fake,
        env={DEEPSEEK_API_KEY: "from-env"},
    )

    value = source.get(DEEPSEEK_API_KEY)
    assert value is not None
    assert value.value == "from-env"
    assert value.source == "env"


def test_keyring_wins_over_legacy_dotenv(tmp_path: Path) -> None:
    fake = FakeKeyring()
    fake.set_password("agent-friend", DEEPSEEK_API_KEY, "from-keyring")
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{DEEPSEEK_API_KEY}=from-dotenv\n", encoding="utf-8")

    source = credential_source_for_mode(
        "legacy-dotenv",
        dotenv_path=dotenv,
        keyring_backend=fake,
        env={},
    )

    value = source.get(DEEPSEEK_API_KEY)
    assert value is not None
    assert value.value == "from-keyring"
    assert value.source == "keyring"


def test_desktop_mode_does_not_read_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{DEEPSEEK_API_KEY}=from-dotenv\n", encoding="utf-8")

    source = credential_source_for_mode("desktop", dotenv_path=dotenv, env={})

    assert source.get(DEEPSEEK_API_KEY) is None


def test_legacy_dotenv_filters_runtime_only_public_url(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                f"{VOICE_BRIDGE_PUBLIC_URL}=https://stale.example.com",
                f"{DEEPSEEK_API_KEY}=from-dotenv",
            ]
        ),
        encoding="utf-8",
    )
    source = LegacyDotenvSource(dotenv)

    assert source.get(VOICE_BRIDGE_PUBLIC_URL) is None
    assert source.get(DEEPSEEK_API_KEY) is not None


def test_status_does_not_expose_secret_value() -> None:
    fake = FakeKeyring()
    store = KeyringSource(backend=fake)
    store.set(DEEPSEEK_API_KEY, "super-secret-value")

    status = store.status(DEEPSEEK_API_KEY)

    assert status.configured is True
    assert status.source == "keyring"
    assert "super-secret-value" not in repr(status)
