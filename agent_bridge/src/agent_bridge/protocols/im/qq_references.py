"""Normalize one QQ native quote into the common quoted-text domain."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from agent.message_references import (
    QuotedTextLocator,
    QuotedTextReference,
    QuotedTextSource,
)
from qqbot_agent_sdk.dto import MSG_TYPE_QUOTE

from agent import Session

from .event import InboundEvent
from .inbound_resolver import InboundResolveResult
from .reference_aliases import IMReferenceAlias, IMReferenceAliasIndex

__all__ = [
    "QQReferenceResolver",
    "qq_current_message_aliases",
    "qq_referenced_message_aliases",
]

_EXT_PAIR_RE = re.compile(
    r"(?P<key>ref_msg_idx|msg_idx|ref_idx)\s*[:=]\s*[\"']?(?P<value>REFIDX_[^\"',;\s}]+)",
    re.IGNORECASE,
)


class QQReferenceResolver:
    """Resolve QQ's single whole-message quote before the event reaches ``IMRouter``."""

    def __init__(
        self,
        aliases: IMReferenceAliasIndex,
        session_loader: Callable[[str], Session],
    ) -> None:
        self._aliases = aliases
        self._session_loader = session_loader

    async def resolve(
        self,
        im_type: str,
        event: InboundEvent,
        *,
        provider_bind_id: str = "",
    ) -> InboundResolveResult:
        if im_type != "qq" or int(getattr(event, "message_type", 0) or 0) != MSG_TYPE_QUOTE:
            return InboundResolveResult.route(event)

        referenced_aliases = qq_referenced_message_aliases(event)
        entry = self._aliases.resolve(
            im_type=im_type,
            provider_bind_id=provider_bind_id,
            chat_scope=event.chat_scope,
            chat_id=event.chat_id,
            message_aliases=referenced_aliases,
        )
        direct_text = _direct_quoted_text(event)
        source_events = self._events_for_alias(entry)
        source_event = source_events[0] if source_events else None
        if source_event is not None:
            source_role: Literal["user", "assistant"] = (
                "user" if source_event.type == "user_message" else "assistant"
            )
            source_text = "\n\n".join(
                str(item.payload.get("content", "")) for item in source_events
            )
            quoted_text = direct_text or (source_text if isinstance(source_text, str) else "")
            availability: Literal["available", "unavailable"] = (
                "available" if quoted_text.strip() else "unavailable"
            )
            reference = QuotedTextReference(
                reference_id=f"qq-ref-{uuid4()}",
                quoted_text=quoted_text if availability == "available" else None,
                source_role=source_role,
                availability=availability,
                source=QuotedTextSource(
                    kind="session_message",
                    message_event_uuid=source_event.uuid,
                    text_block_id=source_event.uuid,
                ),
                locator=QuotedTextLocator(kind="whole_message"),
            )
            return InboundResolveResult.route(event, references=(reference,))

        opaque_alias = referenced_aliases[0] if referenced_aliases else event.message_id
        external_key = self._aliases.opaque_key(
            im_type=im_type,
            provider_bind_id=provider_bind_id,
            chat_scope=event.chat_scope,
            chat_id=event.chat_id,
            message_alias=opaque_alias,
        )
        reference = QuotedTextReference(
            reference_id=f"qq-ref-{uuid4()}",
            quoted_text=direct_text or None,
            source_role="unknown",
            availability="available" if direct_text else "unavailable",
            source=QuotedTextSource(
                kind="external_message",
                external_source_key=external_key,
            ),
            locator=QuotedTextLocator(kind="whole_message"),
        )
        return InboundResolveResult.route(event, references=(reference,))

    def _events_for_alias(self, entry: IMReferenceAlias | None) -> list[Any]:
        if entry is None:
            return []
        try:
            session = self._session_loader(entry.session_id)
        except Exception:
            return []
        active = {
            event.uuid: event
            for event in session.active_events
            if event.type in {"user_message", "assistant_message"}
        }
        identities = entry.event_uuids or (entry.event_uuid,)
        if any(identity not in active for identity in identities):
            return []
        return [active[identity] for identity in identities]


def qq_referenced_message_aliases(event: InboundEvent) -> list[str]:
    aliases: list[str] = []
    elements = getattr(event, "msg_elements", None) or []
    if elements:
        value = str(getattr(elements[0], "msg_idx", "") or "")
        if value:
            aliases.append(value)
    ext_values = _message_scene_values(getattr(event, "raw", None))
    aliases.extend(ext_values.get("ref_msg_idx", []))
    aliases.extend(ext_values.get("ref_idx", []))
    reference = _message_reference(getattr(event, "raw", None))
    aliases.extend(reference)
    return list(dict.fromkeys(alias for alias in aliases if alias))


def qq_current_message_aliases(event: InboundEvent) -> list[str]:
    aliases = [event.message_id] if event.message_id else []
    ext_values = _message_scene_values(getattr(event, "raw", None))
    aliases.extend(ext_values.get("msg_idx", []))
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _direct_quoted_text(event: InboundEvent) -> str:
    elements = getattr(event, "msg_elements", None) or []
    if not elements:
        return ""
    content = getattr(elements[0], "content", "")
    return content if isinstance(content, str) and content.strip() else ""


def _message_scene_values(raw: object) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"ref_msg_idx": [], "msg_idx": [], "ref_idx": []}
    if not isinstance(raw, dict):
        return result
    scene = raw.get("message_scene")
    if not isinstance(scene, dict) or not isinstance(scene.get("ext"), list):
        return result
    for item in scene["ext"]:
        if not isinstance(item, str):
            continue
        parsed: object = None
        with suppress(json.JSONDecodeError):
            parsed = json.loads(item)
        if isinstance(parsed, dict):
            for key in result:
                value = parsed.get(key)
                if isinstance(value, str) and value:
                    result[key].append(value)
        for match in _EXT_PAIR_RE.finditer(item):
            key = match.group("key").lower()
            normalized = "ref_idx" if key == "ref_idx" else key
            result[normalized].append(match.group("value"))
    return {key: list(dict.fromkeys(values)) for key, values in result.items()}


def _message_reference(raw: object) -> list[str]:
    if not isinstance(raw, dict):
        return []
    candidate = raw.get("message_reference") or raw.get("messageReference")
    if not isinstance(candidate, dict):
        return []
    values: list[str] = []
    for key in ("ref_msg_idx", "ref_idx", "msg_idx", "message_id", "id"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return list(dict.fromkeys(values))
