"""Credential source implementations."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values

from .credentials import RUNTIME_ONLY_NAMES, CredentialStatus, CredentialValue

DEFAULT_KEYRING_SERVICE = "agent-friend"


class CredentialMissingError(RuntimeError):
    """Raised when a required credential is missing from all enabled sources."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = tuple(names)
        joined = ", ".join(self.names)
        super().__init__(
            f"缺少运行所需凭据：{joined}。请运行 scripts/credentials/run.* 配置，"
            "或在显式 legacy-dotenv 模式下提供存量 .env。"
        )


class CredentialSource(Protocol):
    """Read-only credential source."""

    source_name: str

    def get(self, name: str) -> CredentialValue | None: ...

    def status(self, name: str) -> CredentialStatus: ...


class EnvSource:
    """Credential source backed by the current process environment."""

    source_name = "env"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def get(self, name: str) -> CredentialValue | None:
        value = self._env.get(name, "").strip()
        if not value:
            return None
        return CredentialValue(name=name, value=value, source=self.source_name)

    def status(self, name: str) -> CredentialStatus:
        value = self.get(name)
        return CredentialStatus(
            name=name, configured=value is not None, source=value.source if value else None
        )


class KeyringSource:
    """Credential source backed by Python ``keyring``."""

    source_name = "keyring"

    def __init__(
        self,
        *,
        service_name: str = DEFAULT_KEYRING_SERVICE,
        backend: Any | None = None,
    ) -> None:
        self._service_name = service_name
        self._backend = backend

    def _keyring(self) -> Any:
        if self._backend is not None:
            return self._backend
        try:
            import keyring
        except Exception as e:  # pragma: no cover - depends on local install state
            raise RuntimeError(f"keyring backend unavailable: {e}") from e
        return keyring

    def get(self, name: str) -> CredentialValue | None:
        try:
            raw = self._keyring().get_password(self._service_name, name)
        except Exception:
            return None
        value = (raw or "").strip()
        if not value:
            return None
        return CredentialValue(name=name, value=value, source=self.source_name)

    def status(self, name: str) -> CredentialStatus:
        try:
            raw = self._keyring().get_password(self._service_name, name)
        except Exception as e:
            return CredentialStatus(
                name=name, configured=False, source=self.source_name, error=str(e)
            )
        return CredentialStatus(
            name=name,
            configured=bool((raw or "").strip()),
            source=self.source_name if (raw or "").strip() else None,
        )

    def set(self, name: str, value: str) -> None:
        self._keyring().set_password(self._service_name, name, value)

    def delete(self, name: str) -> None:
        try:
            self._keyring().delete_password(self._service_name, name)
        except Exception as e:
            if "not found" in str(e).lower() or "not exist" in str(e).lower():
                return
            raise


class LegacyDotenvSource:
    """Explicit legacy ``.env`` source.

    Runtime-only values such as ``VOICE_BRIDGE_PUBLIC_URL`` are filtered even in
    legacy mode, so stale tunnel URLs cannot leak back into voice runs.
    """

    source_name = "legacy-dotenv"

    def __init__(
        self,
        path: str | Path = ".env",
        *,
        ignored_names: frozenset[str] = RUNTIME_ONLY_NAMES,
    ) -> None:
        self._path = Path(path)
        self._ignored_names = ignored_names

    def _values(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        data = dotenv_values(self._path)
        values: dict[str, str] = {}
        for key, value in data.items():
            if key is None or key in self._ignored_names or value is None:
                continue
            stripped = value.strip()
            if stripped:
                values[key] = stripped
        return values

    def get(self, name: str) -> CredentialValue | None:
        value = self._values().get(name, "")
        if not value:
            return None
        return CredentialValue(name=name, value=value, source=self.source_name)

    def status(self, name: str) -> CredentialStatus:
        value = self.get(name)
        return CredentialStatus(
            name=name, configured=value is not None, source=value.source if value else None
        )


class CompositeCredentialSource:
    """Resolve credentials from ordered sources."""

    source_name = "composite"

    def __init__(self, sources: Sequence[CredentialSource]) -> None:
        self._sources = tuple(sources)

    @property
    def sources(self) -> tuple[CredentialSource, ...]:
        return self._sources

    def get(self, name: str) -> CredentialValue | None:
        for source in self._sources:
            value = source.get(name)
            if value is not None:
                return value
        return None

    def status(self, name: str) -> CredentialStatus:
        errors: list[str] = []
        for source in self._sources:
            status = source.status(name)
            if status.configured:
                return status
            if status.error:
                errors.append(f"{status.source}: {status.error}")
        return CredentialStatus(
            name=name,
            configured=False,
            error="; ".join(errors) if errors else None,
        )


def optional_secret(source: CredentialSource, name: str) -> str | None:
    value = source.get(name)
    return value.value if value is not None else None


def require_secret(source: CredentialSource, name: str) -> str:
    value = source.get(name)
    if value is None:
        raise CredentialMissingError([name])
    return value.value
