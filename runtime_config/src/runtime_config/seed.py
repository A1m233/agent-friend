"""Trusted-beta credential seed envelope and transactional keyring import."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .credentials import (
    ALL_CREDENTIAL_NAMES,
    OPTIONAL_CREDENTIAL_NAMES,
    REQUIRED_CREDENTIAL_NAMES,
    CredentialValue,
)

SEED_SCHEMA_VERSION = 1
SEED_DISTRIBUTION = "trusted-beta"
SEED_MAGIC = b"AFCS\x01"
SEED_AAD = b"agent-friend:trusted-credential-seed:v1"
SEED_NONCE_SIZE = 12
SEED_KEY_SIZE = 32
MAX_SEED_BYTES = 64 * 1024


class CredentialSeedError(RuntimeError):
    """Safe-to-display seed error that never embeds credential values."""


class CredentialStore(Protocol):
    def get(self, name: str) -> CredentialValue | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


@dataclass(frozen=True)
class CredentialSeedArtifacts:
    payload: bytes
    key: bytes


CredentialSeedValues = dict[str, str | None]


def _validated_credentials(credentials: Mapping[str, object]) -> CredentialSeedValues:
    expected = set(ALL_CREDENTIAL_NAMES)
    actual = set(credentials)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    invalid = sorted(
        name
        for name, value in credentials.items()
        if value is not None and not isinstance(value, str)
    )
    empty = sorted(
        name for name, value in credentials.items() if isinstance(value, str) and not value.strip()
    )
    required_null = sorted(
        name
        for name in REQUIRED_CREDENTIAL_NAMES
        if name in credentials and credentials[name] is None
    )
    optional_null = {
        name
        for name, value in credentials.items()
        if value is None and name in OPTIONAL_CREDENTIAL_NAMES
    }
    unexpected_null = sorted(
        name for name, value in credentials.items() if value is None and name not in optional_null
    )
    if missing or extra or invalid or empty or required_null or unexpected_null:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={','.join(missing)}")
        if extra:
            parts.append(f"extra={','.join(extra)}")
        if invalid:
            parts.append(f"invalid={','.join(invalid)}")
        if empty:
            parts.append(f"empty={','.join(empty)}")
        null_required = sorted(set(required_null) | set(unexpected_null))
        if null_required:
            parts.append(f"required_null={','.join(null_required)}")
        raise CredentialSeedError("credential seed set is invalid: " + "; ".join(parts))
    normalized: CredentialSeedValues = {}
    for name in ALL_CREDENTIAL_NAMES:
        value = credentials[name]
        normalized[name] = value.strip() if isinstance(value, str) else None
    return normalized


def collect_trusted_credentials(store: CredentialStore) -> CredentialSeedValues:
    """Read the canonical registry, allowing only declared optional values to be absent."""

    values: CredentialSeedValues = {}
    missing: list[str] = []
    unreadable: list[str] = []
    for name in ALL_CREDENTIAL_NAMES:
        try:
            resolved = store.get(name)
        except Exception:
            unreadable.append(name)
            continue
        value = resolved.value.strip() if resolved is not None else ""
        if value:
            values[name] = value
        elif name in OPTIONAL_CREDENTIAL_NAMES:
            values[name] = None
        else:
            missing.append(name)
    if missing or unreadable:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={','.join(missing)}")
        if unreadable:
            parts.append(f"unreadable={','.join(unreadable)}")
        raise CredentialSeedError("trusted credential collection failed: " + "; ".join(parts))
    return values


def encrypt_credential_seed(credentials: Mapping[str, object]) -> CredentialSeedArtifacts:
    """Serialize and encrypt the exact managed credential set without writing plaintext."""

    validated = _validated_credentials(credentials)
    plaintext = json.dumps(
        {
            "schema_version": SEED_SCHEMA_VERSION,
            "distribution": SEED_DISTRIBUTION,
            "credentials": validated,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(SEED_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, SEED_AAD)
    payload = SEED_MAGIC + nonce + ciphertext
    if len(payload) > MAX_SEED_BYTES:
        raise CredentialSeedError("credential seed payload exceeds size limit")
    return CredentialSeedArtifacts(payload=payload, key=key)


def decrypt_credential_seed(payload: bytes, key: bytes) -> CredentialSeedValues:
    """Authenticate, decrypt and validate a trusted-beta credential seed."""

    if not payload or len(payload) > MAX_SEED_BYTES:
        raise CredentialSeedError("credential seed payload size is invalid")
    if not payload.startswith(SEED_MAGIC):
        raise CredentialSeedError("credential seed header is invalid")
    if len(key) != SEED_KEY_SIZE:
        raise CredentialSeedError("credential seed key size is invalid")
    nonce_start = len(SEED_MAGIC)
    nonce_end = nonce_start + SEED_NONCE_SIZE
    if len(payload) <= nonce_end:
        raise CredentialSeedError("credential seed payload is truncated")
    try:
        plaintext = AESGCM(key).decrypt(
            payload[nonce_start:nonce_end], payload[nonce_end:], SEED_AAD
        )
    except InvalidTag as exc:
        raise CredentialSeedError("credential seed authentication failed") from exc
    try:
        envelope = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialSeedError("credential seed content is invalid") from exc
    if not isinstance(envelope, dict):
        raise CredentialSeedError("credential seed envelope is invalid")
    if envelope.get("schema_version") != SEED_SCHEMA_VERSION:
        raise CredentialSeedError("credential seed schema is unsupported")
    if envelope.get("distribution") != SEED_DISTRIBUTION:
        raise CredentialSeedError("credential seed distribution is invalid")
    credentials = envelope.get("credentials")
    if not isinstance(credentials, dict) or not all(isinstance(name, str) for name in credentials):
        raise CredentialSeedError("credential seed credentials are invalid")
    return _validated_credentials(credentials)


def install_credential_seed(credentials: Mapping[str, object], store: CredentialStore) -> int:
    """Synchronize the exact seed and restore prior values if an operation fails."""

    validated = _validated_credentials(credentials)
    previous: dict[str, str | None] = {}
    for name in ALL_CREDENTIAL_NAMES:
        try:
            resolved = store.get(name)
        except Exception as exc:
            error_type = type(exc).__name__
            raise CredentialSeedError(
                f"credential snapshot failed for {name} ({error_type})"
            ) from exc
        previous[name] = resolved.value if resolved is not None else None

    written: list[str] = []
    current_name = ""
    try:
        for current_name in ALL_CREDENTIAL_NAMES:
            new_value = validated[current_name]
            if new_value is None:
                if previous[current_name] is None:
                    continue
                store.delete(current_name)
            else:
                store.set(current_name, new_value)
            written.append(current_name)
    except Exception as exc:
        rollback_failed: list[str] = []
        for name in reversed(written):
            try:
                old_value = previous[name]
                if old_value is None:
                    store.delete(name)
                else:
                    store.set(name, old_value)
            except Exception:
                rollback_failed.append(name)
        error_type = type(exc).__name__
        suffix = f"; rollback_failed={','.join(sorted(rollback_failed))}" if rollback_failed else ""
        raise CredentialSeedError(
            f"credential import failed for {current_name} ({error_type}){suffix}"
        ) from exc
    return sum(value is not None for value in validated.values())
