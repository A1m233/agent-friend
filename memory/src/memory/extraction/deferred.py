"""Private durable observation codec; never include these payloads in diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
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


def decode(payload: str) -> ConversationFragment:
    data = json.loads(payload)
    if data["version"] != 1:
        raise ValueError("unsupported deferred observation version")
    raw = data["fragment"]

    def utterance(item: dict[str, Any]) -> Utterance:
        return Utterance(**{**item, "ts": datetime.fromisoformat(item["ts"])})

    context = raw.get("semantic_update_context")
    if context is not None:
        context = SemanticUpdateContext(
            **{
                **context,
                "turns": tuple(
                    SemanticUpdateTurn(tuple(utterance(u) for u in turn["utterances"]))
                    for turn in context["turns"]
                ),
            }
        )
    return ConversationFragment(
        **{
            **raw,
            "utterances": [utterance(u) for u in raw["utterances"]],
            "semantic_update_context": context,
        }
    )
