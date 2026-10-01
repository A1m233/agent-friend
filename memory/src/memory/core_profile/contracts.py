"""Core Profile field registry, rows, projection, and structural validation."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CoreFieldKey = Literal[
    "user.preferred_name",
    "user.current_residences",
    "relationship.kind",
    "relationship.agent_nickname",
]
CoreScopeKind = Literal["user", "persona"]
CoreCardinality = Literal["single", "many"]

_MAX_PROFILE_JSON_CHARS = 64_000


class CoreProfileError(ValueError):
    """A Core Profile row or projection violates the fixed schema."""


@dataclass(frozen=True)
class CoreFieldSpec:
    key: CoreFieldKey
    scope_kind: CoreScopeKind
    cardinality: CoreCardinality
    max_value_chars: int
    label: str


CORE_FIELD_SPECS: dict[CoreFieldKey, CoreFieldSpec] = {
    "user.preferred_name": CoreFieldSpec(
        "user.preferred_name", "user", "single", 80, "用户希望被称为"
    ),
    "user.current_residences": CoreFieldSpec(
        "user.current_residences", "user", "many", 200, "用户当前住处"
    ),
    "relationship.kind": CoreFieldSpec(
        "relationship.kind", "persona", "single", 240, "你与用户的关系"
    ),
    "relationship.agent_nickname": CoreFieldSpec(
        "relationship.agent_nickname", "persona", "single", 80, "用户对你的称呼"
    ),
}


@dataclass(frozen=True)
class CoreProfileEntry:
    id: str
    owner_user_id: str
    scope_kind: CoreScopeKind
    persona_id: str
    field_key: CoreFieldKey
    value: str
    normalized_value: str
    valid_from: datetime
    valid_until: datetime | None
    supersedes_id: str | None
    provenance: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class CoreProfile:
    owner_user_id: str
    persona_id: str
    entries: tuple[CoreProfileEntry, ...]

    def as_dict(self) -> dict[str, object]:
        by_field: dict[str, list[str]] = {}
        for entry in self.entries:
            by_field.setdefault(entry.field_key, []).append(entry.value)
        return {
            "user": {
                "preferred_name": _single(by_field.get("user.preferred_name", [])),
                "current_residences": by_field.get("user.current_residences", []),
            },
            "relationship": {
                "kind": _single(by_field.get("relationship.kind", [])),
                "agent_nickname": _single(by_field.get("relationship.agent_nickname", [])),
            },
        }

    def is_empty(self) -> bool:
        return not self.entries


def build_core_profile(
    entries: list[CoreProfileEntry], *, owner_user_id: str, persona_id: str
) -> CoreProfile:
    active = [
        entry
        for entry in entries
        if entry.deleted_at is None
        and entry.valid_until is None
        and entry.owner_user_id == owner_user_id
        and (
            entry.scope_kind != "persona"
            or (entry.scope_kind == "persona" and entry.persona_id == persona_id)
        )
    ]
    seen_single: set[CoreFieldKey] = set()
    seen_many: set[tuple[CoreFieldKey, str]] = set()
    for entry in active:
        spec = CORE_FIELD_SPECS.get(entry.field_key)
        if spec is None or spec.scope_kind != entry.scope_kind:
            raise CoreProfileError(f"invalid core entry schema: {entry.id}")
        for value_time in (entry.valid_from, entry.created_at, entry.updated_at):
            if not isinstance(value_time, datetime) or value_time.tzinfo is None:
                raise CoreProfileError(f"invalid core timestamp: {entry.id}")
        if not isinstance(entry.provenance, tuple) or any(
            not isinstance(ref, str) for ref in entry.provenance
        ):
            raise CoreProfileError(f"invalid core provenance: {entry.id}")
        value = validate_core_value(entry.field_key, entry.value)
        if entry.normalized_value != normalize_core_value(value):
            raise CoreProfileError(f"invalid normalized core value: {entry.id}")
        if spec.scope_kind == "user" and entry.persona_id:
            raise CoreProfileError(f"user-scoped core entry has persona: {entry.id}")
        if spec.scope_kind == "persona" and not entry.persona_id:
            raise CoreProfileError(f"persona-scoped core entry has no persona: {entry.id}")
        if spec.cardinality == "single":
            if entry.field_key in seen_single:
                raise CoreProfileError(f"multiple active values for {entry.field_key}")
            seen_single.add(entry.field_key)
        else:
            key = (entry.field_key, entry.normalized_value)
            if key in seen_many:
                raise CoreProfileError(f"duplicate active value for {entry.field_key}")
            seen_many.add(key)
    active.sort(key=_entry_sort_key)
    profile = CoreProfile(owner_user_id, persona_id, tuple(active))
    if len(json.dumps(profile.as_dict(), ensure_ascii=False)) > _MAX_PROFILE_JSON_CHARS:
        raise CoreProfileError("core profile exceeds safety ceiling")
    return profile


def validate_core_value(field_key: CoreFieldKey, value: object) -> str:
    spec = CORE_FIELD_SPECS.get(field_key)
    if spec is None:
        raise CoreProfileError(f"unsupported core field: {field_key}")
    if not isinstance(value, str):
        raise CoreProfileError(f"{field_key} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise CoreProfileError(f"{field_key} must not be empty")
    if len(cleaned) > spec.max_value_chars:
        raise CoreProfileError(f"{field_key} exceeds {spec.max_value_chars} characters")
    return cleaned


def normalize_core_value(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _entry_sort_key(entry: CoreProfileEntry) -> tuple[int, str, str]:
    fields = tuple(CORE_FIELD_SPECS)
    return fields.index(entry.field_key), entry.normalized_value, entry.id


def _single(values: list[str]) -> str | None:
    return values[0] if values else None
