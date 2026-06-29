"""Runtime config mode policy."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from .sources import (
    CompositeCredentialSource,
    CredentialSource,
    EnvSource,
    KeyringSource,
    LegacyDotenvSource,
    SettingsFileSource,
)

CONFIG_MODE_ENV = "AGENT_FRIEND_CONFIG_MODE"
ConfigMode = Literal["desktop", "legacy-dotenv", "env-only"]


def normalize_config_mode(raw: str | None) -> ConfigMode:
    value = (raw or "desktop").strip().lower().replace("_", "-")
    if value in ("", "desktop", "keyring"):
        return "desktop"
    if value in ("legacy", "dotenv", "legacy-dotenv"):
        return "legacy-dotenv"
    if value in ("env", "env-only"):
        return "env-only"
    raise ValueError(f"未知 {CONFIG_MODE_ENV}={raw!r}；可选值：desktop / legacy-dotenv / env-only")


def config_mode_from_env(env: Mapping[str, str] | None = None) -> ConfigMode:
    current = env if env is not None else os.environ
    return normalize_config_mode(current.get(CONFIG_MODE_ENV))


def settings_kwargs_for_mode(mode: ConfigMode, dotenv_path: str | Path = ".env") -> dict[str, Any]:
    if mode == "legacy-dotenv":
        return {"_env_file": dotenv_path}
    return {}


def credential_source_for_mode(
    mode: ConfigMode,
    *,
    dotenv_path: str | Path = ".env",
    settings_path: str | Path | None = None,
    keyring_backend: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> CompositeCredentialSource:
    sources: list[CredentialSource] = [EnvSource(env)]
    if mode in ("desktop", "legacy-dotenv"):
        sources.append(SettingsFileSource(settings_path))
        sources.append(KeyringSource(backend=keyring_backend))
    if mode == "legacy-dotenv":
        sources.append(LegacyDotenvSource(dotenv_path))
    return CompositeCredentialSource(sources)
