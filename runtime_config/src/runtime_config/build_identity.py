"""Non-secret packaged build identity exposed to supervised runtime services."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

BUILD_IDENTITY_ENV = "AGENT_FRIEND_BUILD_IDENTITY_JSON"


def canonical_identity_bytes(identity: dict[str, Any]) -> bytes:
    return (
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def runtime_build_identity() -> dict[str, Any] | None:
    raw = os.environ.get(BUILD_IDENTITY_ENV, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("runtime build identity is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("runtime build identity must be an object")
    required = ("buildId", "profileId", "productVersion", "sourceCommit")
    missing = [key for key in required if not isinstance(value.get(key), str) or not value[key]]
    if missing:
        raise RuntimeError("runtime build identity is missing: " + ", ".join(missing))
    return value


def readiness_identity(service: str) -> dict[str, object]:
    identity = runtime_build_identity()
    if identity is None:
        return {"service": service}
    return {
        "service": service,
        "buildIdentitySha256": hashlib.sha256(canonical_identity_bytes(identity)).hexdigest(),
        "buildId": identity["buildId"],
        "profileId": identity["profileId"],
    }
