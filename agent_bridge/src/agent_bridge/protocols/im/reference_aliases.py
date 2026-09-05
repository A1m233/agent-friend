"""Bounded persistent mapping from opaque IM message aliases to session events."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

__all__ = ["IMReferenceAlias", "IMReferenceAliasIndex"]

logger = logging.getLogger(__name__)
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IMReferenceAlias:
    opaque_key: str
    session_id: str
    event_uuid: str
    source_role: Literal["user", "assistant", "unknown"]
    created_at: datetime
    last_accessed_at: datetime
    event_uuids: tuple[str, ...] = ()


class IMReferenceAliasIndex:
    """Atomic user-data index. Message bodies and raw provider identifiers are never stored."""

    def __init__(
        self,
        path: Path,
        *,
        max_items: int = 10_000,
        ttl: timedelta = timedelta(days=7),
    ) -> None:
        self._path = Path(path)
        self._max_items = max(1, max_items)
        self._ttl = ttl
        self._lock = threading.RLock()

    @staticmethod
    def opaque_key(
        *,
        im_type: str,
        provider_bind_id: str,
        chat_scope: str,
        chat_id: str,
        message_alias: str,
    ) -> str:
        canonical = "\x1f".join((im_type, provider_bind_id, chat_scope, chat_id, message_alias))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def put(
        self,
        *,
        im_type: str,
        provider_bind_id: str,
        chat_scope: str,
        chat_id: str,
        message_aliases: list[str] | tuple[str, ...],
        session_id: str,
        event_uuid: str,
        source_role: Literal["user", "assistant", "unknown"],
        event_uuids: tuple[str, ...] = (),
    ) -> None:
        aliases = tuple(dict.fromkeys(alias for alias in message_aliases if alias))
        if not aliases:
            return
        now = datetime.now(UTC)
        with self._lock:
            entries = {item.opaque_key: item for item in self._prune(self._load(), now=now)}
            for alias in aliases:
                key = self.opaque_key(
                    im_type=im_type,
                    provider_bind_id=provider_bind_id,
                    chat_scope=chat_scope,
                    chat_id=chat_id,
                    message_alias=alias,
                )
                previous = entries.get(key)
                entries[key] = IMReferenceAlias(
                    opaque_key=key,
                    session_id=session_id,
                    event_uuid=event_uuid,
                    source_role=source_role,
                    created_at=previous.created_at if previous is not None else now,
                    last_accessed_at=now,
                    event_uuids=tuple(dict.fromkeys(event_uuids)),
                )
            self._save(self._bounded(entries.values()))

    def resolve(
        self,
        *,
        im_type: str,
        provider_bind_id: str,
        chat_scope: str,
        chat_id: str,
        message_aliases: list[str] | tuple[str, ...],
    ) -> IMReferenceAlias | None:
        aliases = tuple(dict.fromkeys(alias for alias in message_aliases if alias))
        if not aliases:
            return None
        now = datetime.now(UTC)
        with self._lock:
            entries = {item.opaque_key: item for item in self._prune(self._load(), now=now)}
            found: IMReferenceAlias | None = None
            for alias in aliases:
                key = self.opaque_key(
                    im_type=im_type,
                    provider_bind_id=provider_bind_id,
                    chat_scope=chat_scope,
                    chat_id=chat_id,
                    message_alias=alias,
                )
                candidate = entries.get(key)
                if candidate is not None:
                    found = replace(candidate, last_accessed_at=now)
                    entries[key] = found
                    break
            self._save(self._bounded(entries.values()))
            return found

    def snapshot(self) -> tuple[IMReferenceAlias, ...]:
        with self._lock:
            return tuple(self._bounded(self._prune(self._load(), now=datetime.now(UTC))))

    def _bounded(self, entries: Any) -> list[IMReferenceAlias]:
        ordered = sorted(entries, key=lambda item: item.last_accessed_at, reverse=True)
        return ordered[: self._max_items]

    def _prune(
        self,
        entries: list[IMReferenceAlias],
        *,
        now: datetime,
    ) -> list[IMReferenceAlias]:
        cutoff = now - self._ttl
        return [item for item in entries if item.last_accessed_at >= cutoff]

    def _load(self) -> list[IMReferenceAlias]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("unsupported reference alias schema")
            values = raw.get("entries")
            if not isinstance(values, list):
                raise ValueError("reference alias entries must be a list")
            return [_entry_from_dict(item) for item in values]
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.warning(
                "IM reference alias index unreadable at %s; treating as empty",
                self._path,
                exc_info=True,
            )
            return []

    def _save(self, entries: list[IMReferenceAlias]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        temp.write_text(
            json.dumps(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "entries": [_entry_to_dict(item) for item in entries],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temp.replace(self._path)


def _entry_to_dict(entry: IMReferenceAlias) -> dict[str, Any]:
    return {
        "opaque_key": entry.opaque_key,
        "session_id": entry.session_id,
        "event_uuid": entry.event_uuid,
        "event_uuids": list(entry.event_uuids),
        "source_role": entry.source_role,
        "created_at": entry.created_at.isoformat(),
        "last_accessed_at": entry.last_accessed_at.isoformat(),
    }


def _entry_from_dict(raw: object) -> IMReferenceAlias:
    if not isinstance(raw, dict):
        raise ValueError("reference alias entry must be an object")
    role = raw.get("source_role")
    source_ids = raw.get("event_uuids", [])
    if not isinstance(source_ids, list) or not all(
        isinstance(item, str) and item for item in source_ids
    ):
        raise ValueError("invalid reference source list")
    if role not in ("user", "assistant", "unknown"):
        raise ValueError("invalid reference alias role")
    return IMReferenceAlias(
        opaque_key=str(raw["opaque_key"]),
        session_id=str(raw["session_id"]),
        event_uuid=str(raw["event_uuid"]),
        source_role=role,
        created_at=datetime.fromisoformat(str(raw["created_at"])),
        last_accessed_at=datetime.fromisoformat(str(raw["last_accessed_at"])),
        event_uuids=tuple(source_ids),
    )
