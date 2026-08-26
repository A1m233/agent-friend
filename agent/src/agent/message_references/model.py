"""Quoted text references shared by desktop, IM adapters, sessions, and prompts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

ReferenceAvailability = Literal["available", "unavailable"]
ReferenceSourceRole = Literal["user", "assistant", "unknown"]
ReferenceSourceKind = Literal["session_message", "external_message"]
ReferenceLocatorKind = Literal["text_range", "whole_message"]


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串或 null")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{field_name} 包含未知字段: {', '.join(unknown)}")


@dataclass(frozen=True)
class QuotedTextSource:
    """Stable source anchor. Provider-specific identifiers stay behind an opaque key."""

    kind: ReferenceSourceKind
    message_event_uuid: str | None = None
    text_block_id: str | None = None
    external_source_key: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("session_message", "external_message"):
            raise ValueError(f"不支持的引用来源 kind: {self.kind!r}")
        if self.kind == "session_message":
            _required_string(self.message_event_uuid, "source.message_event_uuid")
            if self.external_source_key is not None:
                raise ValueError("session_message 不能携带 external_source_key")
        else:
            _required_string(self.external_source_key, "source.external_source_key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message_event_uuid": self.message_event_uuid,
            "text_block_id": self.text_block_id,
            "external_source_key": self.external_source_key,
        }

    @classmethod
    def from_dict(cls, data: object) -> QuotedTextSource:
        if not isinstance(data, dict):
            raise ValueError("source 必须是对象")
        _reject_unknown(
            data,
            {"kind", "message_event_uuid", "text_block_id", "external_source_key"},
            "source",
        )
        kind = data.get("kind")
        if kind not in ("session_message", "external_message"):
            raise ValueError("source.kind 只接受 session_message / external_message")
        return cls(
            kind=kind,
            message_event_uuid=_optional_string(
                data.get("message_event_uuid"), "source.message_event_uuid"
            ),
            text_block_id=_optional_string(data.get("text_block_id"), "source.text_block_id"),
            external_source_key=_optional_string(
                data.get("external_source_key"), "source.external_source_key"
            ),
        )


@dataclass(frozen=True)
class QuotedTextLocator:
    """UI-only position metadata; never projected into model-visible context."""

    kind: ReferenceLocatorKind
    coordinate_space: str | None = None
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        if self.kind == "text_range":
            if self.coordinate_space != "rendered_text_utf16_v1":
                raise ValueError("text_range 只接受 rendered_text_utf16_v1 坐标空间")
            if (
                not isinstance(self.start, int)
                or isinstance(self.start, bool)
                or not isinstance(self.end, int)
                or isinstance(self.end, bool)
                or self.start < 0
                or self.end <= self.start
            ):
                raise ValueError("text_range 必须提供合法的 start/end 半开区间")
            return
        if self.kind != "whole_message":
            raise ValueError(f"不支持的引用 locator kind: {self.kind!r}")
        if self.coordinate_space is not None or self.start is not None or self.end is not None:
            raise ValueError("whole_message 不能携带坐标或 start/end")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "coordinate_space": self.coordinate_space,
            "start": self.start,
            "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: object) -> QuotedTextLocator:
        if not isinstance(data, dict):
            raise ValueError("locator 必须是对象")
        _reject_unknown(data, {"kind", "coordinate_space", "start", "end"}, "locator")
        kind = data.get("kind")
        if kind not in ("text_range", "whole_message"):
            raise ValueError("locator.kind 只接受 text_range / whole_message")
        coordinate_space = data.get("coordinate_space")
        if coordinate_space is not None and not isinstance(coordinate_space, str):
            raise ValueError("locator.coordinate_space 必须是字符串或 null")
        start = data.get("start")
        end = data.get("end")
        return cls(kind=kind, coordinate_space=coordinate_space, start=start, end=end)


@dataclass(frozen=True)
class QuotedTextReference:
    """One ordered quoted-text fact attached to a user message."""

    reference_id: str
    quoted_text: str | None
    source_role: ReferenceSourceRole
    availability: ReferenceAvailability
    source: QuotedTextSource
    locator: QuotedTextLocator

    def __post_init__(self) -> None:
        _required_string(self.reference_id, "reference_id")
        if self.source_role not in ("user", "assistant", "unknown"):
            raise ValueError("source_role 只接受 user / assistant / unknown")
        if self.availability == "available":
            _required_string(self.quoted_text, "quoted_text")
        elif self.availability == "unavailable":
            if self.quoted_text is not None:
                raise ValueError("unavailable 引用不能伪造 quoted_text")
        else:
            raise ValueError("availability 只接受 available / unavailable")
        if self.locator.kind == "text_range" and (
            self.source.kind != "session_message" or not self.source.text_block_id
        ):
            raise ValueError("text_range 必须指向 session_message 的文本块")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "quoted_text": self.quoted_text,
            "source_role": self.source_role,
            "availability": self.availability,
            "source": self.source.to_dict(),
            "locator": self.locator.to_dict(),
        }

    def with_quoted_text(self, quoted_text: str | None) -> QuotedTextReference:
        return replace(self, quoted_text=quoted_text)

    def with_source_role(self, source_role: ReferenceSourceRole) -> QuotedTextReference:
        return replace(self, source_role=source_role)

    @classmethod
    def from_dict(cls, data: object) -> QuotedTextReference:
        if not isinstance(data, dict):
            raise ValueError("引用项必须是对象")
        _reject_unknown(
            data,
            {"reference_id", "quoted_text", "source_role", "availability", "source", "locator"},
            "引用项",
        )
        quoted_text = data.get("quoted_text")
        if quoted_text is not None and not isinstance(quoted_text, str):
            raise ValueError("quoted_text 必须是字符串或 null")
        source_role = data.get("source_role")
        availability = data.get("availability")
        if source_role not in ("user", "assistant", "unknown"):
            raise ValueError("source_role 只接受 user / assistant / unknown")
        if availability not in ("available", "unavailable"):
            raise ValueError("availability 只接受 available / unavailable")
        return cls(
            reference_id=_required_string(data.get("reference_id"), "reference_id"),
            quoted_text=quoted_text,
            source_role=source_role,
            availability=availability,
            source=QuotedTextSource.from_dict(data.get("source")),
            locator=QuotedTextLocator.from_dict(data.get("locator")),
        )


def parse_quoted_text_references(value: object) -> list[QuotedTextReference]:
    """Decode a request/session value without silently dropping malformed entries."""

    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("references 必须是数组")
    result = [
        item if isinstance(item, QuotedTextReference) else QuotedTextReference.from_dict(item)
        for item in value
    ]
    reference_ids = [item.reference_id for item in result]
    if len(set(reference_ids)) != len(reference_ids):
        raise ValueError("同一条消息中的 reference_id 不能重复")
    return result


def quoted_text_references_to_dicts(
    references: Sequence[QuotedTextReference],
) -> list[dict[str, Any]]:
    return [item.to_dict() for item in references]


def quoted_text_references_fingerprint(references: Sequence[QuotedTextReference]) -> str:
    encoded = json.dumps(
        quoted_text_references_to_dicts(references),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
