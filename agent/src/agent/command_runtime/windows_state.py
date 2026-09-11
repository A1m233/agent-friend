"""Read-only state contract for the pinned Codex 0.147.0 sandbox (setup version 5)."""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

SETUP_VERSION = 5
MAX_STATE_BYTES = 64 * 1024


def read_state(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        raw = source.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("sandbox state exceeds limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("sandbox state must be an object")
    return value


def compatible_state(marker: dict[str, object], users: dict[str, object]) -> bool:
    if any(
        type(obj.get("version")) is not int or obj["version"] != SETUP_VERSION
        for obj in (marker, users)
    ):
        return False
    ports = marker.get("proxy_ports", [])
    if not isinstance(ports, list) or any(type(p) is not int or not 0 < p < 65536 for p in ports):
        return False
    if type(marker.get("allow_local_binding", False)) is not bool:
        return False
    for mode in ("online", "offline"):
        name = marker.get(f"{mode}_username")
        record = users.get(mode)
        if not isinstance(name, str) or not name or not isinstance(record, dict):
            return False
        if record.get("username") != name:
            return False
        password = record.get("password")
        if not isinstance(password, str) or not password:
            return False
        try:
            if not base64.b64decode(password, validate=True):
                return False
        except (binascii.Error, ValueError):
            return False
    return True
