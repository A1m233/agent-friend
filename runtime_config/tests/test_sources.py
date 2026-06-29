"""runtime_config credential source tests."""

from __future__ import annotations

from pathlib import Path

from runtime_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MEMORY_MODEL,
    DEEPSEEK_MODEL,
    VOICE_BRIDGE_PUBLIC_URL,
    VOLC_ACCESS_KEY,
    VOLC_RTC_APP_ID,
    VOLC_RTC_APP_KEY,
    VOLC_SECRET_KEY,
    VOLC_SPEECH_ACCESS_TOKEN,
    VOLC_SPEECH_API_KEY,
    VOLC_SPEECH_APP_ID,
    KeyringSource,
    LegacyDotenvSource,
    SettingsFileSource,
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


VOICE_BRIDGE_VOLC_ALIASES = (
    (VOLC_ACCESS_KEY, "VOICE_BRIDGE_VOLC_ACCESS_KEY"),
    (VOLC_SECRET_KEY, "VOICE_BRIDGE_VOLC_SECRET_KEY"),
    (VOLC_RTC_APP_ID, "VOICE_BRIDGE_VOLC_RTC_APP_ID"),
    (VOLC_RTC_APP_KEY, "VOICE_BRIDGE_VOLC_RTC_APP_KEY"),
    (VOLC_SPEECH_APP_ID, "VOICE_BRIDGE_VOLC_SPEECH_APP_ID"),
    (VOLC_SPEECH_ACCESS_TOKEN, "VOICE_BRIDGE_VOLC_SPEECH_ACCESS_TOKEN"),
    (VOLC_SPEECH_API_KEY, "VOICE_BRIDGE_VOLC_SPEECH_API_KEY"),
)


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

    source = credential_source_for_mode(
        "desktop",
        dotenv_path=dotenv,
        keyring_backend=FakeKeyring(),
        env={},
    )

    assert source.get(DEEPSEEK_API_KEY) is None


def test_settings_file_source_resolves_desktop_model_settings(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        """{
  "settings": {
    "deepseekModel": "deepseek/deepseek-v4-flash",
    "deepseekMemoryModel": "deepseek/deepseek-v4-pro"
  }
}
""",
        encoding="utf-8",
    )
    source = SettingsFileSource(settings)

    model = source.get(DEEPSEEK_MODEL)
    memory_model = source.get(DEEPSEEK_MEMORY_MODEL)

    assert model is not None
    assert model.value == "deepseek/deepseek-v4-flash"
    assert model.source == "settings"
    assert memory_model is not None
    assert memory_model.value == "deepseek/deepseek-v4-pro"
    assert memory_model.source == "settings"
    assert source.get(DEEPSEEK_API_KEY) is None


def test_desktop_mode_reads_settings_before_keyring_for_model_settings(tmp_path: Path) -> None:
    fake = FakeKeyring()
    fake.set_password("agent-friend", DEEPSEEK_MEMORY_MODEL, "from-keyring")
    settings = tmp_path / "settings.json"
    settings.write_text(
        """{
  "settings": {
    "deepseekMemoryModel": "deepseek/deepseek-v4-flash"
  }
}
""",
        encoding="utf-8",
    )
    source = credential_source_for_mode(
        "desktop",
        settings_path=settings,
        keyring_backend=fake,
        env={},
    )

    value = source.get(DEEPSEEK_MEMORY_MODEL)

    assert value is not None
    assert value.value == "deepseek/deepseek-v4-flash"
    assert value.source == "settings"


def test_voice_bridge_env_aliases_resolve_as_canonical_credentials() -> None:
    env = {alias: f"from-{alias.lower()}" for _, alias in VOICE_BRIDGE_VOLC_ALIASES}
    source = credential_source_for_mode("desktop", keyring_backend=FakeKeyring(), env=env)

    for canonical, alias in VOICE_BRIDGE_VOLC_ALIASES:
        value = source.get(canonical)

        assert value is not None
        assert value.name == canonical
        assert value.value == env[alias]
        assert value.source == "env"


def test_env_alias_wins_over_keyring_value_for_same_canonical_secret() -> None:
    fake = FakeKeyring()
    fake.set_password("agent-friend", VOLC_SPEECH_API_KEY, "from-keyring")
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=fake,
        env={"VOICE_BRIDGE_VOLC_SPEECH_API_KEY": "from-alias-env"},
    )

    value = source.get(VOLC_SPEECH_API_KEY)

    assert value is not None
    assert value.value == "from-alias-env"
    assert value.source == "env"


def test_keyring_wins_over_legacy_dotenv_alias(tmp_path: Path) -> None:
    fake = FakeKeyring()
    fake.set_password("agent-friend", VOLC_SPEECH_API_KEY, "from-keyring")
    dotenv = tmp_path / ".env"
    dotenv.write_text("VOICE_BRIDGE_VOLC_SPEECH_API_KEY=from-dotenv-alias\n", encoding="utf-8")

    source = credential_source_for_mode(
        "legacy-dotenv",
        dotenv_path=dotenv,
        keyring_backend=fake,
        env={},
    )

    value = source.get(VOLC_SPEECH_API_KEY)

    assert value is not None
    assert value.value == "from-keyring"
    assert value.source == "keyring"


def test_legacy_dotenv_alias_resolves_as_canonical_credential(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("VOICE_BRIDGE_VOLC_SPEECH_API_KEY=from-dotenv-alias\n", encoding="utf-8")
    source = credential_source_for_mode(
        "legacy-dotenv",
        dotenv_path=dotenv,
        keyring_backend=FakeKeyring(),
        env={},
    )

    value = source.get(VOLC_SPEECH_API_KEY)

    assert value is not None
    assert value.name == VOLC_SPEECH_API_KEY
    assert value.value == "from-dotenv-alias"
    assert value.source == "legacy-dotenv"


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
