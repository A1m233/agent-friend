"""Credential-free definitions; remote model names never determine routing."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID


def _identity(value: str) -> None:
    if str(UUID(value)) != value:
        raise ValueError("Connection and model identities must be canonical UUIDs")


def model_reference(connection_id: str, model_id: str) -> str:
    _identity(connection_id)
    _identity(model_id)
    return f"custom/{connection_id}/{model_id}"


@dataclass(frozen=True)
class CustomModel:
    id: str
    name: str
    context_window: int

    def __post_init__(self) -> None:
        _identity(self.id)
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError("Model name must be non-empty and have no surrounding whitespace")
        if any(ord(char) < 32 for char in self.name):
            raise ValueError("Model name cannot contain control characters")
        if type(self.context_window) is not int or self.context_window <= 0:
            raise ValueError("A positive, known context window is required")


@dataclass(frozen=True)
class ConnectionDefinition:
    id: str
    name: str
    base_url: str
    protocol: Literal["chat_completions", "responses"]
    models: tuple[CustomModel, ...]
    credential_revision: str | None = None

    def __post_init__(self) -> None:
        _identity(self.id)
        if self.credential_revision is not None:
            _identity(self.credential_revision)
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError("Connection name must be non-empty")
        if self.protocol not in {"chat_completions", "responses"}:
            raise ValueError("Unsupported connection protocol")
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(char.isspace() for char in self.base_url)
        ):
            raise ValueError("Base URL must be HTTP(S), without credentials, query or fragment")
        _ = url.port  # Reject malformed/out-of-range ports before persistence.
        if url.path.rstrip("/").endswith(("/responses", "/chat/completions")):
            raise ValueError("Provide the API base URL, not a generation endpoint")
        if not isinstance(self.models, tuple) or not self.models:
            raise ValueError("A connection requires an immutable, non-empty model list")
        if len({model.id for model in self.models}) != len(self.models):
            raise ValueError("Duplicate model identity")
        if len({model.name for model in self.models}) != len(self.models):
            raise ValueError("Duplicate remote model name in one connection")

    @property
    def credential_name(self) -> str:
        revision = self.credential_revision or self.id
        return f"CUSTOM_LLM_{self.id}_{revision}_API_KEY"
