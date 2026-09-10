"""runtime_config credential source tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import runtime_config.sources as sources

from runtime_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MEMORY_MODEL,
    DEEPSEEK_MODEL,
    LLM_CHAT_MODEL,
    LLM_CHAT_PROVIDER,
    LLM_MEMORY_MODEL,
    LLM_MEMORY_PROVIDER,
    VOICE_BRIDGE_PUBLIC_URL,
    VOLC_ACCESS_KEY,
    VOLC_RTC_APP_ID,
    VOLC_RTC_APP_KEY,
    VOLC_SECRET_KEY,
    VOLC_SPEECH_ACCESS_TOKEN,
    VOLC_SPEECH_API_KEY,
    VOLC_SPEECH_APP_ID,
    DesktopSettingsReader,
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

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_desktop_settings_reader_distinguishes_valid_empty_from_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    reader = DesktopSettingsReader(path)

    assert reader.read() is None
    path.write_text('{"settings": {}}', encoding="utf-8")
    assert reader.read() == {}
    path.write_text("[]", encoding="utf-8")
    assert reader.read() is None


def test_desktop_settings_reader_supports_wrapped_and_flat_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    reader = DesktopSettingsReader(path)

    path.write_text('{"settings": {"immersiveMode": false}}', encoding="utf-8")
    assert reader.read() == {"immersiveMode": False}
    path.write_text('{"immersiveMode": true}', encoding="utf-8")
    assert reader.read() == {"immersiveMode": True}


def test_settings_file_source_only_projects_memory_defaults_when_settings_are_missing(
    tmp_path: Path,
) -> None:
    source = SettingsFileSource(tmp_path / "missing-settings.json")

    assert source.get(LLM_CHAT_PROVIDER) is None
    assert source.get(LLM_CHAT_MODEL) is None
    assert source.get(LLM_MEMORY_PROVIDER).value == "deepseek"  # type: ignore[union-attr]
    assert source.get(LLM_MEMORY_MODEL).value == "deepseek/deepseek-v4-flash"  # type: ignore[union-attr]


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


def test_settings_file_source_resolves_provider_aware_model_settings(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        """{
  "settings": {
    "chatModel": { "provider": "kimi", "model": "kimi/kimi-k2.6" },
    "memoryModel": { "provider": "deepseek", "model": "deepseek/deepseek-v4-pro" }
  }
}
""",
        encoding="utf-8",
    )
    source = SettingsFileSource(settings)

    chat_provider = source.get(LLM_CHAT_PROVIDER)
    chat_model = source.get(LLM_CHAT_MODEL)
    memory_provider = source.get(LLM_MEMORY_PROVIDER)
    memory_model = source.get(LLM_MEMORY_MODEL)

    assert chat_provider is None
    assert chat_model is None
    assert memory_provider is not None
    assert memory_provider.value == "deepseek"
    assert memory_model is not None
    assert memory_model.value == "deepseek/deepseek-v4-pro"


def test_settings_file_source_reads_shared_desktop_settings_fixture() -> None:
    source = SettingsFileSource(FIXTURES_DIR / "desktop-settings-provider-aware.json")

    chat_provider = source.get(LLM_CHAT_PROVIDER)
    chat_model = source.get(LLM_CHAT_MODEL)
    memory_provider = source.get(LLM_MEMORY_PROVIDER)
    memory_model = source.get(LLM_MEMORY_MODEL)

    assert chat_provider is None
    assert chat_model is None
    assert memory_provider is not None
    assert memory_provider.value == "deepseek"
    assert memory_model is not None
    assert memory_model.value == "deepseek/deepseek-v4-pro"


def test_default_settings_path_matches_desktop_settings_store_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    elif sys.platform == "darwin":
        monkeypatch.setenv("HOME", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    expected_base = (
        tmp_path / "Library" / "Application Support" if sys.platform == "darwin" else tmp_path
    )
    assert (
        sources.default_settings_path()
        == expected_base / "com.agentfriend.desktop" / "settings.json"
    )


def test_settings_file_source_projects_legacy_deepseek_model_settings(
    tmp_path: Path,
) -> None:
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

    assert source.get(LLM_CHAT_PROVIDER) is None
    assert source.get(LLM_CHAT_MODEL) is None
    assert source.get(LLM_MEMORY_PROVIDER).value == "deepseek"  # type: ignore[union-attr]
    assert source.get(LLM_MEMORY_MODEL).value == "deepseek/deepseek-v4-pro"  # type: ignore[union-attr]


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


def test_keyring_source_reads_legacy_rust_windows_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring()
    monkeypatch.setattr(
        sources,
        "_legacy_rust_windows_keyring_value",
        lambda service, name: (
            "from-rust-keyring" if (service, name) == ("agent-friend", DEEPSEEK_API_KEY) else None
        ),
    )
    store = KeyringSource(backend=fake)

    value = store.get(DEEPSEEK_API_KEY)

    assert value is not None
    assert value.value == "from-rust-keyring"
    assert value.source == "keyring"


def test_legacy_rust_windows_keyring_helpers_treat_missing_target_as_empty() -> None:
    name = f"AGENT_FRIEND_TEST_MISSING_{os.getpid()}"

    assert sources._legacy_rust_windows_keyring_value("agent-friend", name) is None
    assert sources._delete_legacy_rust_windows_keyring_value("agent-friend", name) is False


def test_keyring_status_accepts_legacy_rust_windows_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring()
    monkeypatch.setattr(
        sources,
        "_legacy_rust_windows_keyring_value",
        lambda service, name: "from-rust-keyring",
    )
    store = KeyringSource(backend=fake)

    status = store.status(DEEPSEEK_API_KEY)

    assert status.configured is True
    assert status.source == "keyring"


def test_keyring_delete_also_deletes_legacy_rust_windows_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []

    def delete_legacy(service: str, name: str) -> bool:
        deleted.append((service, name))
        return True

    monkeypatch.setattr(
        sources,
        "_delete_legacy_rust_windows_keyring_value",
        delete_legacy,
    )
    store = KeyringSource(backend=FakeKeyring())

    store.delete(DEEPSEEK_API_KEY)

    assert deleted == [("agent-friend", DEEPSEEK_API_KEY)]


def test_memory_effort_projection_preserves_arbitrary_value(tmp_path: Path) -> None:
    import json

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "memoryModel": {
                    "provider": "custom",
                    "model": "custom/example",
                    "reasoningPreset": "effort:vendor-next",
                }
            }
        ),
        encoding="utf-8",
    )
    source = SettingsFileSource(path)
    value = source.get("LLM_MEMORY_REASONING_PRESET")
    assert value is not None and value.value == "effort:vendor-next"
    path.write_text(
        json.dumps({"memoryModel": {"provider": "kimi", "model": "kimi/kimi-k2.6"}}),
        encoding="utf-8",
    )
    assert source.get("LLM_MEMORY_REASONING_PRESET") is None
