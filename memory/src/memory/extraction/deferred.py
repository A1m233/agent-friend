"""Private durable observation codec; never include these payloads in diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from ..contracts import ConversationFragment, SemanticUpdateContext, SemanticUpdateTurn, Utterance
from .reconciler import _observation_id


def observation_id(fragment: ConversationFragment) -> str:
    refs = [u.source_ref for u in fragment.utterances]
    return _observation_id(fragment, refs[0] if refs else None, refs[-1] if refs else None)


def encode(fragment: ConversationFragment) -> str:
    return json.dumps(
        {"version": 1, "fragment": asdict(fragment)},
        ensure_ascii=False,
        default=lambda value: value.isoformat(),
    )


class DeferredObservationError(ValueError):
    """A persisted observation cannot be decoded under a supported contract."""


def decode(payload: str) -> ConversationFragment:
    try:
        return _decode(payload)
    except (ValueError, TypeError, KeyError) as exc:
        raise DeferredObservationError("invalid deferred observation") from exc


def _decode(payload: str) -> ConversationFragment:
    data = json.loads(payload)
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unsupported deferred observation version")
    raw = data["fragment"]
    if not isinstance(raw, dict):
        raise ValueError("fragment must be an object")
    for key in ("session_id", "persona_id", "owner_user_id"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ValueError("invalid fragment identity")
    if not isinstance(raw.get("defer_extraction", False), bool):
        raise ValueError("invalid deferral flag")

    def utterance(item: Any) -> Utterance:
        if not isinstance(item, dict) or item.get("speaker") not in ("user", "agent"):
            raise ValueError("invalid utterance")
        if any(not isinstance(item.get(key), str) for key in ("text", "ts", "source_ref")):
            raise ValueError("invalid utterance fields")
        if not item["source_ref"].startswith(raw["session_id"] + "#"):
            raise ValueError("invalid utterance source")
        ts = datetime.fromisoformat(item["ts"])
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        return Utterance(**{**item, "ts": ts})

    if not isinstance(raw.get("utterances"), list) or not raw["utterances"]:
        raise ValueError("missing observation utterances")
    context = raw.get("semantic_update_context")
    if context is not None:
        if not isinstance(context, dict) or not isinstance(context.get("turns"), list):
            raise ValueError("invalid semantic update context")
        turns = []
        for turn in context["turns"]:
            if not isinstance(turn, dict) or not isinstance(turn.get("utterances"), list):
                raise ValueError("invalid semantic update turn")
            turns.append(SemanticUpdateTurn(tuple(utterance(u) for u in turn["utterances"])))
        for key, value in context.items():
            if key.endswith("source_ref") and value is not None and not isinstance(value, str):
                raise ValueError("invalid semantic context source")
        count = context.get("range_omitted_turn_count", 0)
        if type(count) is not int or count < 0:
            raise ValueError("invalid semantic context count")
        context = SemanticUpdateContext(**{**context, "turns": tuple(turns)})
    return ConversationFragment(
        **{
            **raw,
            "utterances": [utterance(u) for u in raw["utterances"]],
            "semantic_update_context": context,
        }
    )
