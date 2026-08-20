from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from runtime_config.seed import SEED_AAD, SEED_MAGIC, SEED_NONCE_SIZE

from runtime_config import (
    ALL_CREDENTIAL_NAMES,
    AMAP_WEBSERVICE_KEY,
    OPENROUTER_API_KEY,
    OPTIONAL_CREDENTIAL_NAMES,
    QWEATHER_API_HOST,
    QWEATHER_API_KEY,
    REQUIRED_CREDENTIAL_NAMES,
    CredentialSeedError,
    CredentialValue,
    collect_trusted_credentials,
    decrypt_credential_seed,
    encrypt_credential_seed,
    install_credential_seed,
)


def _credentials(prefix: str = "secret") -> dict[str, str]:
    return {name: f"{prefix}-{index}" for index, name in enumerate(ALL_CREDENTIAL_NAMES)}


def _credentials_without_optional(prefix: str = "secret") -> dict[str, str | None]:
    credentials: dict[str, str | None] = {
        name: value for name, value in _credentials(prefix).items()
    }
    for name in OPTIONAL_CREDENTIAL_NAMES:
        credentials[name] = None
    return credentials


class FakeStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.fail_set: str | None = None
        self.fail_delete: str | None = None

    def get(self, name: str) -> CredentialValue | None:
        value = self.values.get(name)
        return CredentialValue(name=name, value=value, source="fake") if value else None

    def set(self, name: str, value: str) -> None:
        if name == self.fail_set:
            raise RuntimeError("backend rejected redacted value")
        self.values[name] = value

    def delete(self, name: str) -> None:
        if name == self.fail_delete:
            raise RuntimeError("backend delete failed")
        self.values.pop(name, None)


def test_seed_round_trip_and_ciphertext_do_not_contain_plaintext() -> None:
    credentials = _credentials_without_optional()
    artifacts = encrypt_credential_seed(credentials)

    assert decrypt_credential_seed(artifacts.payload, artifacts.key) == credentials
    for value in credentials.values():
        if value is None:
            continue
        assert value.encode() not in artifacts.payload
        assert value.encode() not in artifacts.key


def test_seed_rejects_incomplete_extra_and_empty_sets() -> None:
    credentials = _credentials()
    missing_name = REQUIRED_CREDENTIAL_NAMES[0]
    credentials.pop(missing_name)
    with pytest.raises(CredentialSeedError, match=f"missing={missing_name}"):
        encrypt_credential_seed(credentials)

    credentials = _credentials()
    credentials["UNMANAGED_SECRET"] = "value"
    with pytest.raises(CredentialSeedError, match="extra=UNMANAGED_SECRET"):
        encrypt_credential_seed(credentials)

    credentials = _credentials()
    credentials[missing_name] = " "
    with pytest.raises(CredentialSeedError, match=f"empty={missing_name}"):
        encrypt_credential_seed(credentials)

    credentials_with_null_required: dict[str, str | None] = {
        name: value for name, value in _credentials().items()
    }
    credentials_with_null_required[missing_name] = None
    with pytest.raises(CredentialSeedError, match=f"required_null={missing_name}"):
        encrypt_credential_seed(credentials_with_null_required)


def test_seed_rejects_wrong_key_tampering_and_wrong_distribution() -> None:
    artifacts = encrypt_credential_seed(_credentials())
    with pytest.raises(CredentialSeedError, match="authentication failed"):
        decrypt_credential_seed(artifacts.payload, AESGCM.generate_key(bit_length=256))

    tampered = bytearray(artifacts.payload)
    tampered[-1] ^= 1
    with pytest.raises(CredentialSeedError, match="authentication failed"):
        decrypt_credential_seed(bytes(tampered), artifacts.key)

    nonce = artifacts.payload[len(SEED_MAGIC) : len(SEED_MAGIC) + SEED_NONCE_SIZE]
    plaintext = json.dumps(
        {
            "schema_version": 1,
            "distribution": "ordinary",
            "credentials": _credentials(),
        },
        separators=(",", ":"),
    ).encode()
    payload = SEED_MAGIC + nonce + AESGCM(artifacts.key).encrypt(nonce, plaintext, SEED_AAD)
    with pytest.raises(CredentialSeedError, match="distribution is invalid"):
        decrypt_credential_seed(payload, artifacts.key)


def test_collect_trusted_credentials_requires_operational_keyring() -> None:
    credentials = _credentials()
    store = FakeStore(credentials)
    assert collect_trusted_credentials(store) == credentials

    for optional_name in OPTIONAL_CREDENTIAL_NAMES:
        store.values.pop(optional_name)
    assert collect_trusted_credentials(store) == _credentials_without_optional()

    missing_required = REQUIRED_CREDENTIAL_NAMES[0]
    store.values.pop(missing_required)
    with pytest.raises(CredentialSeedError, match=f"missing={missing_required}"):
        collect_trusted_credentials(store)


def test_amap_key_is_managed_but_optional_for_trusted_seed() -> None:
    assert AMAP_WEBSERVICE_KEY in ALL_CREDENTIAL_NAMES
    assert AMAP_WEBSERVICE_KEY in OPTIONAL_CREDENTIAL_NAMES
    assert AMAP_WEBSERVICE_KEY not in REQUIRED_CREDENTIAL_NAMES


def test_openrouter_key_is_a_managed_model_credential() -> None:
    assert OPENROUTER_API_KEY in ALL_CREDENTIAL_NAMES
    assert OPENROUTER_API_KEY in REQUIRED_CREDENTIAL_NAMES


@pytest.mark.parametrize("name", [QWEATHER_API_KEY, QWEATHER_API_HOST])
def test_qweather_credentials_are_managed_but_optional_for_trusted_seed(name: str) -> None:
    assert name in ALL_CREDENTIAL_NAMES
    assert name in OPTIONAL_CREDENTIAL_NAMES
    assert name not in REQUIRED_CREDENTIAL_NAMES


def test_install_seed_overwrites_complete_set() -> None:
    store = FakeStore(_credentials("old"))
    replacement = _credentials("new")

    assert install_credential_seed(replacement, store) == len(ALL_CREDENTIAL_NAMES)
    assert store.values == replacement


def test_install_seed_deletes_absent_optional_value() -> None:
    store = FakeStore(_credentials("old"))
    replacement = _credentials_without_optional("new")

    assert install_credential_seed(replacement, store) == len(REQUIRED_CREDENTIAL_NAMES)
    assert store.values == {name: value for name, value in replacement.items() if value is not None}


def test_install_seed_skips_delete_when_optional_value_is_already_absent() -> None:
    store = FakeStore(_credentials("old"))
    for name in OPTIONAL_CREDENTIAL_NAMES:
        store.values.pop(name)
        store.fail_delete = name

    replacement = _credentials_without_optional("new")
    assert install_credential_seed(replacement, store) == len(REQUIRED_CREDENTIAL_NAMES)
    assert store.values == {name: value for name, value in replacement.items() if value is not None}


def test_install_seed_rolls_back_existing_and_new_values() -> None:
    old = {name: f"old-{index}" for index, name in enumerate(ALL_CREDENTIAL_NAMES[:3])}
    store = FakeStore(old)
    store.fail_set = ALL_CREDENTIAL_NAMES[4]

    with pytest.raises(CredentialSeedError, match=f"failed for {ALL_CREDENTIAL_NAMES[4]}"):
        install_credential_seed(_credentials("new"), store)

    assert store.values == old


def test_install_seed_error_never_contains_values_when_rollback_fails() -> None:
    store = FakeStore()
    store.fail_set = ALL_CREDENTIAL_NAMES[2]
    store.fail_delete = ALL_CREDENTIAL_NAMES[1]
    credentials = _credentials("never-print")

    with pytest.raises(CredentialSeedError) as error:
        install_credential_seed(credentials, store)

    message = str(error.value)
    assert "rollback_failed=" in message
    for value in credentials.values():
        assert value not in message
