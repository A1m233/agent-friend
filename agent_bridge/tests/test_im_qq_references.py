"""QQ native quote normalization and opaque alias persistence tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.qq_references import (
    QQReferenceResolver,
    qq_current_message_aliases,
    qq_referenced_message_aliases,
)
from agent_bridge.protocols.im.reference_aliases import IMReferenceAliasIndex
from qqbot_agent_sdk.dto import MSG_TYPE_QUOTE, MsgElement

from agent import Session


def _event(
    *,
    elements: list[MsgElement] | None = None,
    raw: dict[str, object] | None = None,
) -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="OPENID-USER",
        user_id="OPENID-USER",
        chat_scope="c2c",
        content="继续解释",
        message_id="MSG-CURRENT",
        timestamp="2026-08-27T00:00:00Z",
        message_type=MSG_TYPE_QUOTE,
        msg_elements=elements or [],
        raw=raw,
    )


def _put_alias(index: IMReferenceAliasIndex, alias: str = "REFIDX-SOURCE") -> None:
    index.put(
        im_type="qq",
        provider_bind_id="BOT-1",
        chat_scope="c2c",
        chat_id="OPENID-USER",
        message_aliases=[alias],
        session_id="session-1",
        event_uuid="event-source",
        source_role="assistant",
    )


@pytest.mark.asyncio
async def test_qq_quote_prefers_direct_snapshot_and_recovers_session_anchor(tmp_path: Path) -> None:
    aliases = IMReferenceAliasIndex(tmp_path / "references.json")
    _put_alias(aliases)
    session = SimpleNamespace(
        active_events=[
            SimpleNamespace(
                uuid="event-source",
                type="assistant_message",
                payload={"content": "session snapshot"},
            )
        ]
    )
    resolver = QQReferenceResolver(aliases, lambda _session_id: cast(Session, session))

    result = await resolver.resolve(
        "qq",
        _event(elements=[MsgElement(msg_idx="REFIDX-SOURCE", content="QQ direct snapshot")]),
        provider_bind_id="BOT-1",
    )

    assert result.action == "route"
    assert len(result.references) == 1
    reference = result.references[0]
    assert reference.quoted_text == "QQ direct snapshot"
    assert reference.source_role == "assistant"
    assert reference.source.message_event_uuid == "event-source"
    assert reference.locator.kind == "whole_message"


@pytest.mark.asyncio
async def test_qq_quote_recovers_snapshot_from_alias_when_payload_has_no_body(
    tmp_path: Path,
) -> None:
    aliases = IMReferenceAliasIndex(tmp_path / "references.json")
    _put_alias(aliases)
    session = SimpleNamespace(
        active_events=[
            SimpleNamespace(
                uuid="event-source",
                type="assistant_message",
                payload={"content": "persisted answer"},
            )
        ]
    )
    resolver = QQReferenceResolver(aliases, lambda _session_id: cast(Session, session))

    result = await resolver.resolve(
        "qq",
        _event(elements=[MsgElement(msg_idx="REFIDX-SOURCE")]),
        provider_bind_id="BOT-1",
    )

    assert result.references[0].quoted_text == "persisted answer"
    assert result.references[0].availability == "available"


@pytest.mark.asyncio
async def test_unknown_qq_quote_is_preserved_as_unavailable_external_reference(
    tmp_path: Path,
) -> None:
    aliases = IMReferenceAliasIndex(tmp_path / "references.json")
    resolver = QQReferenceResolver(
        aliases,
        lambda _session_id: cast(Session, SimpleNamespace(active_events=[])),
    )
    event = _event(
        raw={
            "message_scene": {
                "ext": ['{"ref_msg_idx":"REFIDX-UNKNOWN","msg_idx":"REFIDX-CURRENT"}']
            }
        }
    )

    result = await resolver.resolve("qq", event, provider_bind_id="BOT-1")

    reference = result.references[0]
    assert reference.availability == "unavailable"
    assert reference.quoted_text is None
    assert reference.source.kind == "external_message"
    assert reference.source.external_source_key != "REFIDX-UNKNOWN"
    assert qq_referenced_message_aliases(event) == ["REFIDX-UNKNOWN"]
    assert qq_current_message_aliases(event) == ["MSG-CURRENT", "REFIDX-CURRENT"]


def test_alias_index_is_bounded_persistent_and_does_not_store_provider_ids(tmp_path: Path) -> None:
    path = tmp_path / "references.json"
    index = IMReferenceAliasIndex(path, max_items=2)
    for number in range(3):
        index.put(
            im_type="qq",
            provider_bind_id="BOT-SECRET",
            chat_scope="c2c",
            chat_id="CHAT-SECRET",
            message_aliases=[f"REFIDX-SECRET-{number}"],
            session_id="session-1",
            event_uuid=f"event-{number}",
            source_role="user",
        )

    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert len(persisted["entries"]) == 2
    assert len(IMReferenceAliasIndex(path, max_items=2).snapshot()) == 2
    encoded = path.read_text(encoding="utf-8")
    assert "BOT-SECRET" not in encoded
    assert "CHAT-SECRET" not in encoded
    assert "REFIDX-SECRET" not in encoded
