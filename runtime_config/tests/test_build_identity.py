from __future__ import annotations

import hashlib
import json

import pytest
from runtime_config.build_identity import readiness_identity, runtime_build_identity


def test_runtime_build_identity_is_absent_in_source_mode(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_FRIEND_BUILD_IDENTITY_JSON", raising=False)
    assert runtime_build_identity() is None
    assert readiness_identity("bridge") == {"service": "bridge"}


def test_readiness_identity_is_canonical_and_non_secret(monkeypatch) -> None:
    identity = {
        "schemaVersion": 1,
        "productVersion": "1.2.3",
        "buildId": "build-123",
        "sourceCommit": "a" * 40,
        "sourceTreeSha256": "b" * 64,
        "dirty": False,
        "buildMode": "formal",
        "distribution": "ordinary",
        "profileId": "production",
    }
    raw = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    monkeypatch.setenv("AGENT_FRIEND_BUILD_IDENTITY_JSON", raw)

    result = readiness_identity("voice")

    assert result == {
        "service": "voice",
        "buildIdentitySha256": hashlib.sha256((raw + "\n").encode()).hexdigest(),
        "buildId": "build-123",
        "profileId": "production",
    }


def test_invalid_runtime_identity_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_FRIEND_BUILD_IDENTITY_JSON", "[]")
    with pytest.raises(RuntimeError, match="must be an object"):
        readiness_identity("bridge")
