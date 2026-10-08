"""Versioned JSON DTOs containing only the existing, verified recovery state."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any, cast

from ..events import ROLLOUT_EVENT_TYPES, Event
from ..session import Session

if TYPE_CHECKING:
    from ..rollout import RolloutCommitState

# Bump when rollout recovery, model-history or context semantics change.
RECOVERY_SEMANTICS_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1


def encode_state(session: Session, commit: RolloutCommitState) -> dict[str, Any]:
    from ..rollout import serialize_epoch, serialize_model_context, serialize_witness

    compaction = session.latest_compaction()
    return {
        "events": [
            json.loads(event.to_jsonl())
            for event in session.events
            if event.type not in ROLLOUT_EVENT_TYPES
        ],
        "commit": {
            "rollout_id": commit.rollout_id,
            "segment_id": commit.segment_id,
            "commit_id": commit.commit_id,
            "messages": copy.deepcopy(list(commit.serialized_messages)),
            "active_event_uuids": list(commit.active_event_uuids),
            "history_generation": commit.history_generation,
            "provider_request_count": commit.provider_request_count,
            "model_context": serialize_model_context(commit.model_context),
            "epoch_manifest": serialize_epoch(
                commit.cache_epoch,
                history_generation=commit.history_generation,
                compaction_id=compaction.uuid if compaction is not None else None,
            ),
            "boundary": commit.boundary,
            "turn_terminal": commit.turn_terminal,
            "final_wire_witness": serialize_witness(commit.final_wire_witness),
            "cache_diagnostics": copy.deepcopy(commit.cache_diagnostics),
            "exact_eligible": commit.exact_eligible,
            "exact_ineligibility_reason": commit.exact_ineligibility_reason,
        },
    }


def decode_state(value: object, session_id: str) -> Session:
    from ..rollout import (
        RolloutCommitState,
        deserialize_epoch,
        deserialize_message,
        deserialize_model_context,
        deserialize_witness,
        finish_rollout_restore,
    )

    if not isinstance(value, dict):
        raise ValueError("invalid snapshot state")
    raw_events, raw = value.get("events"), value.get("commit")
    if not isinstance(raw_events, list) or not isinstance(raw, dict):
        raise ValueError("invalid snapshot events/commit")
    events = [Event.from_jsonl(json.dumps(item, ensure_ascii=False)) for item in raw_events]
    if any(event.type in ROLLOUT_EVENT_TYPES for event in events):
        raise ValueError("snapshot is not a business projection")
    session = Session.from_events(events)
    if session.session_id != session_id or session.has_unresolved_tool_transactions():
        raise ValueError("snapshot session mismatch or unresolved tool transaction")
    messages, active = raw.get("messages"), raw.get("active_event_uuids")
    if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
        raise ValueError("invalid snapshot messages")
    if not isinstance(active, list) or any(not isinstance(item, str) for item in active):
        raise ValueError("invalid active event identities")
    for key in ("rollout_id", "segment_id", "commit_id", "boundary"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ValueError("invalid snapshot commit identity")
    for key in ("history_generation", "provider_request_count"):
        if type(raw.get(key)) is not int or raw[key] < 0:
            raise ValueError("invalid snapshot generation")
    if (
        raw.get("turn_terminal") is not True
        or raw.get("exact_eligible") is not True
        or raw.get("exact_ineligibility_reason") is not None
        or not isinstance(raw.get("model_context"), dict)
    ):
        raise ValueError("snapshot is not exactly restorable")
    diagnostics = raw.get("cache_diagnostics")
    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise ValueError("invalid cache diagnostics")
    commit = RolloutCommitState(
        rollout_id=raw["rollout_id"],
        segment_id=raw["segment_id"],
        commit_id=raw["commit_id"],
        messages=tuple(deserialize_message(item) for item in messages),
        serialized_messages=tuple(cast(list[dict[str, Any]], messages)),
        active_event_uuids=tuple(active),
        history_generation=raw["history_generation"],
        provider_request_count=raw["provider_request_count"],
        model_context=deserialize_model_context(raw["model_context"]),
        cache_epoch=deserialize_epoch(raw.get("epoch_manifest")),
        boundary=raw["boundary"],
        turn_terminal=True,
        final_wire_witness=deserialize_witness(raw.get("final_wire_witness")),
        cache_diagnostics=diagnostics,
        exact_eligible=True,
        exact_ineligibility_reason=None,
    )
    restored = finish_rollout_restore(session, commit)
    if not restored.exact_continuation_candidate:
        raise ValueError("snapshot recovery eligibility changed")
    session.restored_rollout = restored
    return session
