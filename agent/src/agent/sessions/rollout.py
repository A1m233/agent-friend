"""Durable model-transcript rollout embedded in one session JSONL stream."""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import uuid4

from llm_providers import ProviderRequestWitness

from ..messages import Message
from .cache_epoch import CacheEnvelopeState, PreparedCacheEpoch, SessionCacheEpoch
from .events import Event
from .model_context import (
    CalendarState,
    ModelContextSnapshot,
    SessionModelContext,
)
from .model_history import SessionModelHistory, model_relevant_event_uuids
from .query import CancelCheck, check_cancel
from .session import Session
from .store import SessionStore, append_session_events

ROLLOUT_SCHEMA_VERSION = 1

RolloutBoundary = Literal[
    "legacy_migration",
    "user_input_accepted",
    "provider_input_accepted",
    "assistant_completed",
    "tool_call_received",
    "tool_result_completed",
    "model_output_rejected",
    "manual_checkpoint",
]

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "credential",
    "client_secret",
    "password",
    "host_permission_credential",
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(label: str, value: object) -> str:
    payload = _canonical(value).encode("utf-8")
    return sha256(f"agent-friend:{label}:v1\0".encode() + payload).hexdigest()


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        redacted = 0
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                redacted += 1
                continue
            sanitized, nested = _sanitize_value(raw_value)
            result[key] = sanitized
            redacted += nested
        return result, redacted
    if isinstance(value, (list, tuple)):
        result_items: list[Any] = []
        redacted = 0
        for item in value:
            sanitized, nested = _sanitize_value(item)
            result_items.append(sanitized)
            redacted += nested
        return result_items, redacted
    if value is None or isinstance(value, (str, int, float, bool)):
        return value, 0
    return str(value), 0


def serialize_message(message: Message) -> tuple[dict[str, Any], int]:
    """Serialize one adopted Message while filtering credential-shaped metadata keys."""

    raw = message.to_dict()
    sanitized_meta, redacted = _sanitize_value(raw.get("meta", {}))
    raw["meta"] = sanitized_meta
    return raw, redacted


def deserialize_message(value: object) -> Message:
    if not isinstance(value, dict):
        raise ValueError("rollout message must be an object")
    role = value.get("role")
    content = value.get("content")
    timestamp = value.get("timestamp")
    if role not in {"system", "user", "assistant", "tool"}:
        raise ValueError(f"unsupported rollout message role: {role!r}")
    if not isinstance(content, str) or not isinstance(timestamp, str):
        raise ValueError("rollout message content/timestamp invalid")
    return Message.from_dict(cast(dict[str, Any], value))


def serialize_model_context(context: SessionModelContext | ModelContextSnapshot) -> dict[str, Any]:
    snapshot = context.snapshot if isinstance(context, SessionModelContext) else context
    calendar = snapshot.calendar
    return {
        "initialized": sorted(snapshot.initialized),
        "calendar": (
            {
                "local_date": calendar.local_date.isoformat(),
                "timezone": calendar.timezone,
            }
            if calendar is not None
            else None
        ),
        "surface": snapshot.surface,
        "channel": snapshot.channel,
        "approval_mode": snapshot.approval_mode,
        "approval_version": snapshot.approval_version,
        "runtime_capabilities": snapshot.runtime_capabilities,
        "capability_catalog": snapshot.capability_catalog,
        "skill_bindings": snapshot.skill_bindings,
    }


def deserialize_model_context(value: object) -> SessionModelContext:
    if not isinstance(value, dict):
        return SessionModelContext()
    raw_calendar = value.get("calendar")
    calendar = None
    if isinstance(raw_calendar, dict):
        raw_date = raw_calendar.get("local_date")
        timezone = raw_calendar.get("timezone")
        if isinstance(raw_date, str) and isinstance(timezone, str):
            from datetime import date

            calendar = CalendarState(date.fromisoformat(raw_date), timezone)
    initialized = value.get("initialized", [])
    allowed = {
        "calendar",
        "surface",
        "channel",
        "permission",
        "runtime_capabilities",
        "capability_catalog",
        "skill_bindings",
    }
    return SessionModelContext(
        ModelContextSnapshot(
            initialized=cast(
                Any,
                frozenset(
                    item for item in initialized if isinstance(item, str) and item in allowed
                ),
            ),
            calendar=calendar,
            surface=value.get("surface") if isinstance(value.get("surface"), str) else None,
            channel=value.get("channel") if isinstance(value.get("channel"), str) else None,
            approval_mode=(
                value.get("approval_mode") if isinstance(value.get("approval_mode"), str) else None
            ),
            approval_version=(
                value.get("approval_version")
                if isinstance(value.get("approval_version"), int)
                else None
            ),
            runtime_capabilities=(
                value.get("runtime_capabilities")
                if isinstance(value.get("runtime_capabilities"), str)
                else None
            ),
            capability_catalog=(
                value.get("capability_catalog")
                if isinstance(value.get("capability_catalog"), str)
                else None
            ),
            skill_bindings=value.get("skill_bindings")
            if isinstance(value.get("skill_bindings"), str)
            else None,
        )
    )


def _manifest_from_parts(
    *,
    epoch_id: str,
    generation: int,
    reason: str,
    envelope: CacheEnvelopeState | None,
    history_generation: int,
    compaction_id: str | None,
) -> dict[str, Any]:
    redacted_fields = 0
    manifest: dict[str, Any] = {
        "version": 1,
        "cache_epoch_id": epoch_id,
        "generation": generation,
        "reason": reason,
        "transcript_schema_version": ROLLOUT_SCHEMA_VERSION,
        "history_generation": history_generation,
        "compaction_id": compaction_id,
        "envelope": None,
        "redacted_fields": 0,
    }
    if envelope is not None:
        canonical_fields: dict[str, str] = {}
        for key, raw in {
            "tools": envelope.tools,
            "settings": envelope.settings,
            "model_route": envelope.model_route,
        }.items():
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                canonical_fields[key] = raw
                continue
            sanitized, count = _sanitize_value(parsed)
            redacted_fields += count
            canonical_fields[key] = _canonical(sanitized)
        manifest["envelope"] = {
            "persona_id": envelope.persona_id,
            "system_prompt": envelope.system_prompt,
            **canonical_fields,
        }
    manifest["redacted_fields"] = redacted_fields
    manifest["fingerprint"] = _digest("epoch-manifest", manifest)
    return manifest


def serialize_epoch(
    epoch: SessionCacheEpoch,
    *,
    history_generation: int,
    compaction_id: str | None,
) -> dict[str, Any]:
    return _manifest_from_parts(
        epoch_id=epoch.epoch_id,
        generation=epoch.generation,
        reason=epoch.reason,
        envelope=epoch.envelope,
        history_generation=history_generation,
        compaction_id=compaction_id,
    )


def serialize_prepared_epoch(
    prepared: PreparedCacheEpoch,
    *,
    history_generation: int,
    compaction_id: str | None,
) -> dict[str, Any]:
    return _manifest_from_parts(
        epoch_id=prepared.epoch_id,
        generation=prepared.update.generation,
        reason=prepared.update.reason,
        envelope=prepared.envelope,
        history_generation=history_generation,
        compaction_id=compaction_id,
    )


def deserialize_epoch(value: object) -> SessionCacheEpoch:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("unsupported epoch manifest")
    fingerprint = value.get("fingerprint")
    unsigned = dict(value)
    unsigned.pop("fingerprint", None)
    if not isinstance(fingerprint, str) or fingerprint != _digest("epoch-manifest", unsigned):
        raise ValueError("epoch manifest fingerprint mismatch")
    epoch_id = value.get("cache_epoch_id")
    generation = value.get("generation")
    reason = value.get("reason")
    if (
        not isinstance(epoch_id, str)
        or not isinstance(generation, int)
        or not isinstance(reason, str)
    ):
        raise ValueError("epoch manifest identity invalid")
    raw_envelope = value.get("envelope")
    envelope = None
    if raw_envelope is not None:
        if not isinstance(raw_envelope, dict):
            raise ValueError("epoch envelope invalid")
        required = ("system_prompt", "tools", "settings", "model_route")
        if any(not isinstance(raw_envelope.get(key), str) for key in required):
            raise ValueError("epoch envelope fields invalid")
        persona_id = raw_envelope.get("persona_id")
        envelope = CacheEnvelopeState(
            persona_id=persona_id if isinstance(persona_id, str) else None,
            system_prompt=raw_envelope["system_prompt"],
            tools=raw_envelope["tools"],
            settings=raw_envelope["settings"],
            model_route=raw_envelope["model_route"],
        )
    return SessionCacheEpoch(
        generation=generation,
        reason=reason,
        envelope=envelope,
        epoch_id=epoch_id,
    )


def serialize_witness(witness: ProviderRequestWitness | None) -> dict[str, Any] | None:
    if witness is None:
        return None
    return {
        "version": witness.version,
        "provider_id": witness.provider_id,
        "transport": witness.transport,
        "transform_identity": witness.transform_identity,
        "transcript_field": witness.transcript_field,
        "transcript_item_digests": list(witness.transcript_item_digests),
        "transcript_item_roles": list(witness.transcript_item_roles),
        "transcript_item_domains": list(witness.transcript_item_domains),
        "envelope_digest": witness.envelope_digest,
        "request_digest": witness.request_digest,
        "request_bytes": witness.request_bytes,
        "contributor_mapping_precision": witness.contributor_mapping_precision,
    }


def deserialize_witness(value: object) -> ProviderRequestWitness | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("final-wire witness invalid")
    list_fields = (
        "transcript_item_digests",
        "transcript_item_roles",
        "transcript_item_domains",
    )
    if any(
        not isinstance(value.get(key), list)
        or any(not isinstance(item, str) for item in value[key])
        for key in list_fields
    ):
        raise ValueError("final-wire witness item fields invalid")
    return ProviderRequestWitness(
        provider_id=str(value.get("provider_id", "")),
        transport=str(value.get("transport", "")),
        transform_identity=str(value.get("transform_identity", "")),
        transcript_field=(
            value.get("transcript_field")
            if isinstance(value.get("transcript_field"), str)
            else None
        ),
        transcript_item_digests=tuple(value["transcript_item_digests"]),
        transcript_item_roles=tuple(value["transcript_item_roles"]),
        transcript_item_domains=tuple(value["transcript_item_domains"]),
        envelope_digest=(
            value.get("envelope_digest") if isinstance(value.get("envelope_digest"), str) else None
        ),
        request_digest=str(value.get("request_digest", "")),
        request_bytes=int(value.get("request_bytes", 0)),
        contributor_mapping_precision=str(value.get("contributor_mapping_precision", "generic")),
        version=int(value.get("version", 1)),
    )


@dataclass(frozen=True)
class RolloutCommitState:
    rollout_id: str
    segment_id: str
    commit_id: str
    messages: tuple[Message, ...]
    serialized_messages: tuple[dict[str, Any], ...]
    active_event_uuids: tuple[str, ...]
    history_generation: int
    provider_request_count: int
    model_context: SessionModelContext
    cache_epoch: SessionCacheEpoch
    boundary: str
    turn_terminal: bool
    final_wire_witness: ProviderRequestWitness | None
    cache_diagnostics: dict[str, Any] | None
    exact_eligible: bool
    exact_ineligibility_reason: str | None


@dataclass(frozen=True)
class RolloutRestoreResult:
    disposition: str
    reason: str
    history: SessionModelHistory
    model_context: SessionModelContext
    cache_epoch: SessionCacheEpoch
    commit: RolloutCommitState | None = None
    exact_continuation_candidate: bool = False
    migration_required: bool = False
    runtime_blocked: bool = False


def _business_baseline(
    session: Session,
    *,
    disposition: str,
    reason: str,
    migration_required: bool = False,
) -> RolloutRestoreResult:
    history = SessionModelHistory.from_business_session(session, reason=reason)
    epoch = SessionCacheEpoch(reason=reason)
    return RolloutRestoreResult(
        disposition=disposition,
        reason=reason,
        history=history,
        model_context=SessionModelContext(),
        cache_epoch=epoch,
        migration_required=migration_required,
    )


def _no_rollout_baseline(session: Session) -> RolloutRestoreResult:
    if session.business_llm_messages:
        return _business_baseline(
            session,
            disposition="legacy_migration_pending",
            reason="legacy_migration",
            migration_required=True,
        )
    return _business_baseline(
        session,
        disposition="fresh",
        reason="session_start",
    )


def _degraded_restore(
    session: Session,
    committed: RolloutCommitState | None,
    reason: str,
) -> RolloutRestoreResult:
    if committed is None:
        return RolloutRestoreResult(
            disposition=reason,
            reason=reason,
            history=SessionModelHistory(last_reason=reason),
            model_context=SessionModelContext(),
            cache_epoch=SessionCacheEpoch(reason=reason),
            runtime_blocked=True,
        )
    committed = replace(
        committed, serialized_messages=tuple(copy.deepcopy(committed.serialized_messages))
    )
    history = SessionModelHistory.from_persisted(
        committed.messages,
        active_event_uuids=committed.active_event_uuids,
        generation=committed.history_generation,
        provider_request_count=committed.provider_request_count,
        reason=reason,
    )
    epoch = copy.deepcopy(committed.cache_epoch)
    epoch.advance(reason)
    return RolloutRestoreResult(
        disposition=reason,
        reason=reason,
        history=history,
        model_context=committed.model_context,
        cache_epoch=epoch,
        commit=committed,
    )


def restore_rollout(session: Session, *, cancel_check: CancelCheck = None) -> RolloutRestoreResult:
    """Apply only complete, hash-valid rollout commits from the session stream."""

    item_events: dict[str, tuple[Event, dict[str, Any], Message, bytes | None]] = {}
    cached_canonical_bytes = 0
    committed: RolloutCommitState | None = None
    # Same v1 canonical JSON digest, retained before the list's closing bracket.
    # Appends extend that prefix; rewrites start a new one. Every commit is still checked.
    transcript_prefix = sha256(b"agent-friend:model-transcript:v1\0[")
    transcript_items = 0
    for event in session.events:
        check_cancel(cancel_check)
        if event.type == "rollout_segment":
            if event.payload.get("schema_version") != ROLLOUT_SCHEMA_VERSION:
                return _degraded_restore(session, committed, "incompatible_rollout_schema")
            continue
        if event.type == "model_transcript_item":
            if event.payload.get("schema_version") != ROLLOUT_SCHEMA_VERSION:
                continue
            raw_message = event.payload.get("message")
            try:
                message = deserialize_message(raw_message)
            except (KeyError, TypeError, ValueError):
                continue
            canonical_message = _canonical(raw_message).encode("utf-8")
            if (
                event.payload.get("message_sha256")
                != sha256(b"agent-friend:model-message:v1\0" + canonical_message).hexdigest()
            ):
                continue
            previous_item = item_events.get(event.uuid)
            if previous_item is not None and previous_item[3] is not None:
                cached_canonical_bytes -= len(previous_item[3])
            cached = (
                canonical_message
                if cached_canonical_bytes + len(canonical_message) <= 8 * 1024 * 1024
                else None
            )
            cached_canonical_bytes += len(cached) if cached is not None else 0
            item_events[event.uuid] = (
                event,
                cast(dict[str, Any], raw_message),
                message,
                cached,
            )
            continue
        if event.type != "rollout_commit":
            continue
        payload = event.payload
        if payload.get("schema_version") != ROLLOUT_SCHEMA_VERSION:
            return _degraded_restore(session, committed, "incompatible_rollout_schema")
        base_commit_id = payload.get("base_commit_id")
        if base_commit_id != (committed.commit_id if committed is not None else None):
            return _degraded_restore(session, committed, "corrupt_committed_history")
        relation = payload.get("relation")
        refs = payload.get("item_event_uuids")
        if relation not in {"append", "rewrite"} or not isinstance(refs, list):
            return _degraded_restore(session, committed, "corrupt_committed_history")
        if any(not isinstance(ref, str) or ref not in item_events for ref in refs):
            return _degraded_restore(session, committed, "corrupt_committed_history")
        selected = [item_events[ref] for ref in refs]
        previous_serialized = committed.serialized_messages if committed is not None else ()
        previous_messages = committed.messages if committed is not None else ()
        serialized = (
            [*previous_serialized, *(raw for _, raw, _, _ in selected)]
            if relation == "append"
            else [raw for _, raw, _, _ in selected]
        )
        messages = (
            [*previous_messages, *(message for _, _, message, _ in selected)]
            if relation == "append"
            else [message for _, _, message, _ in selected]
        )
        if payload.get("item_count") != len(messages):
            return _degraded_restore(session, committed, "corrupt_committed_history")
        if relation == "rewrite":
            transcript_prefix = sha256(b"agent-friend:model-transcript:v1\0[")
            transcript_items = 0
        for item_event, raw, message, cached_message in selected:
            check_cancel(cancel_check)
            if transcript_items:
                transcript_prefix.update(b",")
            transcript_prefix.update(
                cached_message if cached_message is not None else _canonical(raw).encode("utf-8")
            )
            # Do not retain a second serialized history after its first commit.
            cached_item = item_events[item_event.uuid][3]
            if cached_item is not None:
                cached_canonical_bytes -= len(cached_item)
            item_events[item_event.uuid] = (item_event, raw, message, None)
            transcript_items += 1
        transcript_digest = transcript_prefix.copy()
        transcript_digest.update(b"]")
        if payload.get("transcript_sha256") != transcript_digest.hexdigest():
            return _degraded_restore(session, committed, "corrupt_committed_history")
        raw_active = payload.get("active_event_uuids", [])
        if not isinstance(raw_active, list) or any(
            not isinstance(item, str) for item in raw_active
        ):
            return _degraded_restore(session, committed, "corrupt_committed_history")
        try:
            epoch = deserialize_epoch(payload.get("epoch_manifest"))
            context = deserialize_model_context(payload.get("model_context"))
            witness = deserialize_witness(payload.get("final_wire_witness"))
        except (TypeError, ValueError):
            return _degraded_restore(session, committed, "corrupt_committed_history")
        cache_diagnostics = payload.get("cache_diagnostics")
        committed = RolloutCommitState(
            rollout_id=str(payload.get("rollout_id", "")),
            segment_id=str(payload.get("segment_id", "")),
            commit_id=str(payload.get("commit_id", "")),
            messages=tuple(messages),
            # Replay only reads these values. Isolate once at the return boundary,
            # rather than copying every cumulative historical prefix.
            serialized_messages=tuple(serialized),
            active_event_uuids=tuple(raw_active),
            history_generation=int(payload.get("history_generation", 0)),
            provider_request_count=int(payload.get("provider_request_count", 0)),
            model_context=context,
            cache_epoch=epoch,
            boundary=str(payload.get("boundary", "unknown")),
            turn_terminal=payload.get("turn_terminal") is True,
            final_wire_witness=witness,
            cache_diagnostics=(
                copy.deepcopy(cache_diagnostics) if isinstance(cache_diagnostics, dict) else None
            ),
            exact_eligible=payload.get("exact_eligible", True) is True,
            exact_ineligibility_reason=(
                payload.get("exact_ineligibility_reason")
                if isinstance(payload.get("exact_ineligibility_reason"), str)
                else None
            ),
        )

    check_cancel(cancel_check)
    if committed is None:
        return _no_rollout_baseline(session)

    committed = replace(
        committed, serialized_messages=tuple(copy.deepcopy(committed.serialized_messages))
    )
    history = SessionModelHistory.from_persisted(
        committed.messages,
        active_event_uuids=committed.active_event_uuids,
        generation=committed.history_generation,
        provider_request_count=committed.provider_request_count,
        reason="rollout_restored",
    )
    business_history_matches = committed.active_event_uuids == model_relevant_event_uuids(session)
    tail_is_clean = not session.load_diagnostics.unhandled_tail_corruption
    exact = (
        committed.turn_terminal
        and committed.exact_eligible
        and business_history_matches
        and tail_is_clean
    )
    if exact:
        return RolloutRestoreResult(
            disposition="exact",
            reason=committed.cache_epoch.reason,
            history=history,
            model_context=committed.model_context,
            cache_epoch=committed.cache_epoch,
            commit=committed,
            exact_continuation_candidate=True,
        )

    if (
        committed.exact_ineligibility_reason == "legacy_migration"
        and business_history_matches
        and tail_is_clean
    ):
        return RolloutRestoreResult(
            disposition="legacy_migrated",
            reason="legacy_migration",
            history=history,
            model_context=committed.model_context,
            cache_epoch=committed.cache_epoch,
            commit=committed,
        )

    if session.load_diagnostics.unhandled_tail_corruption:
        disposition = reason = "truncated_tail"
    elif not committed.turn_terminal:
        disposition = "interrupted"
        reason = "interrupted_turn"
    elif not business_history_matches:
        disposition = "interrupted"
        reason = "business_history_changed"
    elif not committed.exact_eligible:
        disposition = "interrupted"
        reason = committed.exact_ineligibility_reason or "credential_redacted"
    else:
        disposition = "interrupted"
        reason = "business_history_changed"
    epoch = copy.deepcopy(committed.cache_epoch)
    epoch.advance(reason)
    return RolloutRestoreResult(
        disposition=disposition,
        reason=reason,
        history=history,
        model_context=committed.model_context,
        cache_epoch=epoch,
        commit=committed,
    )


@dataclass
class _PendingSnapshot:
    serialized: list[dict[str, Any]]
    relation: Literal["append", "rewrite"]
    item_event_uuids: list[str]
    segment_id: str
    redacted_fields: int
    candidate_hash: str


@dataclass
class SessionRolloutRecorder:
    """Append-only recorder sharing the product session JSONL and writer lease."""

    store: SessionStore
    session: Session
    restored: RolloutCommitState | None = None
    recovery_reason: str = "session_start"
    starts_after_corruption: bool = False
    rollout_id: str = field(init=False)
    segment_id: str = field(init=False)
    commit_id: str | None = field(init=False)
    _committed_serialized: list[dict[str, Any]] = field(init=False, repr=False)
    _pending: _PendingSnapshot | None = field(default=None, init=False, repr=False)
    _pending_witness: ProviderRequestWitness | None = field(default=None, init=False, repr=False)
    _pending_cache_diagnostics: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.rollout_id = self.restored.rollout_id if self.restored is not None else str(uuid4())
        self.segment_id = self.restored.segment_id if self.restored is not None else ""
        self.commit_id = self.restored.commit_id if self.restored is not None else None
        self._committed_serialized = (
            copy.deepcopy(list(self.restored.serialized_messages))
            if self.restored is not None
            else []
        )
        if self.starts_after_corruption:
            self._start_segment(self.recovery_reason, starts_after_corruption=True)

    def _append(self, events: list[Event]) -> None:
        append_session_events(self.store, self.session.session_id, events)
        for event in events:
            self.session.append(event)

    def _start_segment(self, reason: str, *, starts_after_corruption: bool = False) -> None:
        self.segment_id = str(uuid4())
        event = Event(
            type="rollout_segment",
            uuid=self.segment_id,
            ts=_utc_now(),
            payload={
                "schema_version": ROLLOUT_SCHEMA_VERSION,
                "rollout_id": self.rollout_id,
                "segment_id": self.segment_id,
                "parent_commit_id": self.commit_id,
                "reason": reason,
                "starts_after_corruption": starts_after_corruption,
            },
        )
        self._append([event])

    def _prepare_snapshot(
        self, messages: list[Message], *, reason: str
    ) -> tuple[_PendingSnapshot, list[Event]]:
        serialized: list[dict[str, Any]] = []
        redacted = 0
        for message in messages:
            raw, count = serialize_message(message)
            serialized.append(raw)
            redacted += count
        prefix = (
            len(serialized) >= len(self._committed_serialized)
            and serialized[: len(self._committed_serialized)] == self._committed_serialized
        )
        relation: Literal["append", "rewrite"] = "append" if prefix else "rewrite"
        selected = serialized[len(self._committed_serialized) :] if prefix else serialized
        if not self.segment_id or relation == "rewrite":
            self._start_segment(reason)
        start_ordinal = len(self._committed_serialized) if relation == "append" else 0
        source_ids = {event.uuid for event in self.session.events}
        item_events: list[Event] = []
        for offset, (raw, message) in enumerate(
            zip(
                selected,
                messages[start_ordinal:] if relation == "append" else messages,
                strict=True,
            )
        ):
            event = Event(
                type="model_transcript_item",
                uuid=str(uuid4()),
                ts=_utc_now(),
                payload={
                    "schema_version": ROLLOUT_SCHEMA_VERSION,
                    "rollout_id": self.rollout_id,
                    "segment_id": self.segment_id,
                    "ordinal": start_ordinal + offset,
                    "message": raw,
                    "message_sha256": _digest("model-message", raw),
                    "source_event_uuids": [message.uuid] if message.uuid in source_ids else [],
                },
            )
            item_events.append(event)
        pending = _PendingSnapshot(
            serialized=serialized,
            relation=relation,
            item_event_uuids=[event.uuid for event in item_events],
            segment_id=self.segment_id,
            redacted_fields=redacted,
            candidate_hash=_digest("model-transcript", serialized),
        )
        return pending, item_events

    def stage_request(
        self,
        *,
        history: tuple[Message, ...],
        model_context: ModelContextSnapshot,
        cache_epoch: PreparedCacheEpoch,
        history_generation: int,
        run_id: str | None,
    ) -> None:
        with self._lock:
            pending, item_events = self._prepare_snapshot(list(history), reason="provider_request")
            compaction = self.session.latest_compaction()
            prepared = Event(
                type="rollout_request_prepared",
                uuid=str(uuid4()),
                ts=_utc_now(),
                payload={
                    "schema_version": ROLLOUT_SCHEMA_VERSION,
                    "rollout_id": self.rollout_id,
                    "segment_id": pending.segment_id,
                    "base_commit_id": self.commit_id,
                    "relation": pending.relation,
                    "item_event_uuids": list(pending.item_event_uuids),
                    "item_count": len(pending.serialized),
                    "transcript_sha256": pending.candidate_hash,
                    "model_context": serialize_model_context(model_context),
                    "epoch_manifest": serialize_prepared_epoch(
                        cache_epoch,
                        history_generation=history_generation,
                        compaction_id=compaction.uuid if compaction is not None else None,
                    ),
                    "run_id": run_id,
                    "redacted_fields": pending.redacted_fields,
                },
            )
            self._append([*item_events, prepared])
            self._pending = pending

    def note_final_wire(
        self,
        witness: ProviderRequestWitness | None,
        cache_diagnostics: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            self._pending_witness = witness
            sanitized, _ = _sanitize_value(cache_diagnostics or {})
            self._pending_cache_diagnostics = cast(dict[str, Any], sanitized)

    def commit(
        self,
        *,
        history: SessionModelHistory,
        model_context: SessionModelContext,
        cache_epoch: SessionCacheEpoch,
        boundary: RolloutBoundary,
        turn_terminal: bool,
        exact_ineligibility_reason: str | None = None,
        context_events: list[Event] | None = None,
    ) -> None:
        with self._lock:
            context_events = list(context_events or [])
            serialized_now = [serialize_message(item)[0] for item in history.items]
            candidate_hash = _digest("model-transcript", serialized_now)
            item_events: list[Event] = []
            pending = self._pending
            if pending is None or pending.candidate_hash != candidate_hash:
                pending, item_events = self._prepare_snapshot(
                    history.snapshot(),
                    reason="history_rewrite" if history.last_relation == "rewrite" else boundary,
                )
            compaction = next(
                (event for event in reversed(context_events) if event.type == "compaction"),
                self.session.latest_compaction(),
            )
            epoch_manifest = serialize_epoch(
                cache_epoch,
                history_generation=history.generation,
                compaction_id=compaction.uuid if compaction is not None else None,
            )
            redacted_fields = pending.redacted_fields + int(
                epoch_manifest.get("redacted_fields", 0)
            )
            effective_ineligibility_reason = exact_ineligibility_reason
            if redacted_fields and effective_ineligibility_reason is None:
                effective_ineligibility_reason = "credential_redacted"
            new_commit_id = str(uuid4())
            commit = Event(
                type="rollout_commit",
                uuid=new_commit_id,
                ts=_utc_now(),
                payload={
                    "schema_version": ROLLOUT_SCHEMA_VERSION,
                    "rollout_id": self.rollout_id,
                    "segment_id": pending.segment_id,
                    "commit_id": new_commit_id,
                    "base_commit_id": self.commit_id,
                    "relation": pending.relation,
                    "item_event_uuids": list(pending.item_event_uuids),
                    "item_count": len(pending.serialized),
                    "transcript_sha256": pending.candidate_hash,
                    "active_event_uuids": [
                        *history.active_event_uuids,
                        *(event.uuid for event in context_events),
                    ],
                    "history_generation": history.generation,
                    "provider_request_count": history.provider_request_count,
                    "boundary": boundary,
                    "turn_terminal": turn_terminal,
                    "model_context": serialize_model_context(model_context),
                    "epoch_manifest": epoch_manifest,
                    "final_wire_witness": serialize_witness(self._pending_witness),
                    "cache_diagnostics": copy.deepcopy(self._pending_cache_diagnostics),
                    "exact_eligible": effective_ineligibility_reason is None,
                    "exact_ineligibility_reason": effective_ineligibility_reason,
                    "redacted_fields": redacted_fields,
                },
            )
            self._append([*item_events, *context_events, commit])
            self.segment_id = pending.segment_id
            self.commit_id = new_commit_id
            self._committed_serialized = copy.deepcopy(pending.serialized)
            self._pending = None
            if turn_terminal:
                self._pending_witness = None
                self._pending_cache_diagnostics = None

    def commit_legacy_migration(
        self,
        *,
        history: SessionModelHistory,
        model_context: SessionModelContext,
        cache_epoch: SessionCacheEpoch,
    ) -> None:
        """Persist the one-time degraded baseline before an old session becomes writable."""

        self.commit(
            history=history,
            model_context=model_context,
            cache_epoch=cache_epoch,
            boundary="legacy_migration",
            turn_terminal=True,
            exact_ineligibility_reason="legacy_migration",
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ROLLOUT_SCHEMA_VERSION",
    "RolloutCommitState",
    "RolloutRestoreResult",
    "SessionRolloutRecorder",
    "deserialize_witness",
    "restore_rollout",
    "serialize_message",
    "serialize_witness",
]
