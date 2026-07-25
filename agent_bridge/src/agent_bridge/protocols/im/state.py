"""Persistent non-secret IM routing and capability state."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

__all__ = [
    "ControlCapability",
    "IMBindingKey",
    "IMControlCapabilityState",
    "IMSessionBinding",
    "IMStateStore",
]

logger = logging.getLogger(__name__)

ControlCapability = Literal["unknown", "supported", "unsupported"]
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IMBindingKey:
    im_type: str
    provider_bind_id: str
    chat_scope: str
    chat_id: str


@dataclass(frozen=True)
class IMSessionBinding:
    key: IMBindingKey
    session_id: str
    updated_at: datetime


@dataclass(frozen=True)
class IMControlCapabilityState:
    im_type: str
    provider_bind_id: str
    status: ControlCapability = "unknown"
    checked_at: datetime | None = None


class IMStateStore:
    """Atomic JSON store for IM session bindings and observed controls capability."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    def get_binding(self, key: IMBindingKey) -> IMSessionBinding | None:
        with self._lock:
            payload = self._load()
            for raw in payload["bindings"]:
                binding = _binding_from_dict(raw)
                if binding.key == key:
                    return binding
        return None

    def set_binding(self, key: IMBindingKey, session_id: str) -> IMSessionBinding:
        binding = IMSessionBinding(key=key, session_id=session_id, updated_at=datetime.now(UTC))
        with self._lock:
            payload = self._load()
            payload["bindings"] = [
                raw for raw in payload["bindings"] if _binding_from_dict(raw).key != key
            ]
            payload["bindings"].append(_binding_to_dict(binding))
            self._save(payload)
        return binding

    def delete_binding(self, key: IMBindingKey) -> None:
        with self._lock:
            payload = self._load()
            next_bindings = [
                raw for raw in payload["bindings"] if _binding_from_dict(raw).key != key
            ]
            if len(next_bindings) == len(payload["bindings"]):
                return
            payload["bindings"] = next_bindings
            self._save(payload)

    def capability(self, im_type: str, provider_bind_id: str) -> IMControlCapabilityState:
        with self._lock:
            payload = self._load()
            for raw in payload["capabilities"]:
                state = _capability_from_dict(raw)
                if state.im_type == im_type and state.provider_bind_id == provider_bind_id:
                    return state
        return IMControlCapabilityState(im_type=im_type, provider_bind_id=provider_bind_id)

    def set_capability(
        self,
        im_type: str,
        provider_bind_id: str,
        status: ControlCapability,
    ) -> IMControlCapabilityState:
        state = IMControlCapabilityState(
            im_type=im_type,
            provider_bind_id=provider_bind_id,
            status=status,
            checked_at=datetime.now(UTC),
        )
        with self._lock:
            payload = self._load()
            payload["capabilities"] = [
                raw
                for raw in payload["capabilities"]
                if not (
                    str(raw.get("im_type", "")) == im_type
                    and str(raw.get("provider_bind_id", "")) == provider_bind_id
                )
            ]
            payload["capabilities"].append(_capability_to_dict(state))
            self._save(payload)
        return state

    def delete_provider(self, im_type: str, provider_bind_id: str) -> None:
        with self._lock:
            payload = self._load()
            bindings = [
                raw
                for raw in payload["bindings"]
                if not (
                    str(raw.get("im_type", "")) == im_type
                    and str(raw.get("provider_bind_id", "")) == provider_bind_id
                )
            ]
            capabilities = [
                raw
                for raw in payload["capabilities"]
                if not (
                    str(raw.get("im_type", "")) == im_type
                    and str(raw.get("provider_bind_id", "")) == provider_bind_id
                )
            ]
            if bindings == payload["bindings"] and capabilities == payload["capabilities"]:
                return
            payload["bindings"] = bindings
            payload["capabilities"] = capabilities
            self._save(payload)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return cast(dict[str, Any], json.loads(json.dumps(self._load())))

    def replace(self, payload: dict[str, Any]) -> None:
        normalized = _normalize_payload(payload)
        # Decode every entry before replacing the current state.
        for raw in normalized["bindings"]:
            _binding_from_dict(raw)
        for raw in normalized["capabilities"]:
            _capability_from_dict(raw)
        with self._lock:
            self._save(normalized)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_payload()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return _normalize_payload(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning(
                "IM state unreadable at %s; treating as empty", self._path, exc_info=True
            )
            return _empty_payload()

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self._path)


def _empty_payload() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "bindings": [], "capabilities": []}


def _normalize_payload(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("IM state root must be an object")
    if int(raw.get("schema_version", 0)) != _SCHEMA_VERSION:
        raise ValueError("unsupported IM state schema")
    bindings = raw.get("bindings", [])
    capabilities = raw.get("capabilities", [])
    if not isinstance(bindings, list) or not isinstance(capabilities, list):
        raise ValueError("IM state collections must be lists")
    return {
        "schema_version": _SCHEMA_VERSION,
        "bindings": bindings,
        "capabilities": capabilities,
    }


def _binding_to_dict(binding: IMSessionBinding) -> dict[str, Any]:
    return {
        **asdict(binding.key),
        "session_id": binding.session_id,
        "updated_at": binding.updated_at.isoformat(),
    }


def _binding_from_dict(raw: object) -> IMSessionBinding:
    if not isinstance(raw, dict):
        raise ValueError("binding must be an object")
    return IMSessionBinding(
        key=IMBindingKey(
            im_type=str(raw["im_type"]),
            provider_bind_id=str(raw["provider_bind_id"]),
            chat_scope=str(raw["chat_scope"]),
            chat_id=str(raw["chat_id"]),
        ),
        session_id=str(raw["session_id"]),
        updated_at=datetime.fromisoformat(str(raw["updated_at"])),
    )


def _capability_to_dict(state: IMControlCapabilityState) -> dict[str, Any]:
    return {
        "im_type": state.im_type,
        "provider_bind_id": state.provider_bind_id,
        "status": state.status,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
    }


def _capability_from_dict(raw: object) -> IMControlCapabilityState:
    if not isinstance(raw, dict):
        raise ValueError("capability must be an object")
    status = str(raw.get("status", "unknown"))
    if status not in ("unknown", "supported", "unsupported"):
        raise ValueError(f"invalid control capability: {status}")
    checked_raw = raw.get("checked_at")
    return IMControlCapabilityState(
        im_type=str(raw["im_type"]),
        provider_bind_id=str(raw["provider_bind_id"]),
        status=status,  # type: ignore[arg-type]
        checked_at=datetime.fromisoformat(str(checked_raw)) if checked_raw else None,
    )
