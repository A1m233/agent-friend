"""Strict, reference-addressed, atomic application of extraction proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ..core_profile import (
    CORE_FIELD_SPECS,
    CoreProfileEntry,
    normalize_core_value,
    validate_core_value,
)
from ..store import EpisodicRow, SemanticRow
from .context import ExtractionContext
from .result import ExtractionOutput, ExtractionResult

if TYPE_CHECKING:
    from ..contracts import ConversationFragment

__all__ = ["ExtractionApplyError", "Reconciler"]

_USER_SAID_BONUS = 0.15


class ExtractionApplyError(ValueError):
    """The proposed batch violated a reference or Core schema contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Reconciler:
    def __init__(
        self,
        store: object,
        *,
        user_said_bonus: float = _USER_SAID_BONUS,
        normalize_metadata: bool = True,
    ) -> None:
        self._store = store
        self._bonus = user_said_bonus
        # Compatibility-only argument. New Semantic rows are always ordinary.
        self._normalize_metadata = normalize_metadata

    def apply(
        self,
        output: ExtractionOutput,
        fragment: ConversationFragment,
        context: ExtractionContext | list[SemanticRow],
    ) -> ExtractionResult:
        extraction_context = (
            context
            if isinstance(context, ExtractionContext)
            else ExtractionContext.from_existing(context)
        )
        _validate_output(output, extraction_context)

        now = datetime.now(UTC)
        source_refs = [utterance.source_ref for utterance in fragment.utterances]
        first_ref = source_refs[0] if source_refs else None
        last_ref = source_refs[-1] if source_refs else None
        observation_id = _observation_id(fragment, first_ref, last_ref)
        output_sha256 = hashlib.sha256(
            json.dumps(asdict(output), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        episodic_rows = _episodic_rows(output, fragment, now)
        episodic_ids = [row.id for row in episodic_rows]
        provenance = [*source_refs, *episodic_ids]
        added_semantic: list[str] = []
        superseded_semantic: list[str] = []
        added_core: list[str] = []
        replaced_core: list[str] = []
        removed_core: list[str] = []

        with self._store.extraction_batch(  # type: ignore[attr-defined]
            observation_id=observation_id,
            session_id=fragment.session_id,
            first_source_ref=first_ref,
            last_source_ref=last_ref,
            output_sha256=output_sha256,
            committed_at=now,
        ) as tx:
            if tx is None:
                return ExtractionResult(
                    session_id=fragment.session_id,
                    observation_id=observation_id,
                    committed=False,
                    duplicate=True,
                )

            for update in output.core_updates:
                spec = CORE_FIELD_SPECS[update.field]
                target = (
                    extraction_context.core_ref_map[update.target_ref]
                    if update.target_ref is not None
                    else None
                )
                if target is not None and not tx.core_is_active(target.id):
                    raise ExtractionApplyError(
                        "stale_core_ref", f"stale Core ref: {update.target_ref}"
                    )
                if update.op == "remove":
                    assert target is not None
                    if not tx.end_core(target.id, now):
                        raise ExtractionApplyError("stale_core_ref", update.target_ref or "")
                    removed_core.append(update.field)
                    continue

                assert update.value is not None
                value = validate_core_value(update.field, update.value)
                new_row = CoreProfileEntry(
                    id=str(uuid4()),
                    owner_user_id=fragment.owner_user_id,
                    scope_kind=spec.scope_kind,
                    persona_id="" if spec.scope_kind == "user" else fragment.persona_id,
                    field_key=update.field,
                    value=value,
                    normalized_value=normalize_core_value(value),
                    valid_from=now,
                    valid_until=None,
                    supersedes_id=target.id if target is not None else None,
                    provenance=tuple(provenance),
                    created_at=now,
                    updated_at=now,
                )
                if target is not None:
                    if not tx.end_core(target.id, now):
                        raise ExtractionApplyError("stale_core_ref", update.target_ref or "")
                    replaced_core.append(update.field)
                else:
                    added_core.append(update.field)
                tx.add_core(new_row)

            if output.core_updates:
                tx.validate_core_profile(
                    owner_user_id=fragment.owner_user_id,
                    persona_id=fragment.persona_id,
                )

            for op in output.semantic_ops:
                semantic_target = (
                    extraction_context.semantic_ref_map[op.target_ref]
                    if op.target_ref is not None
                    else None
                )
                if semantic_target is not None:
                    if not tx.semantic_is_active(semantic_target.id) or not tx.end_semantic(
                        semantic_target.id, now
                    ):
                        raise ExtractionApplyError(
                            "stale_semantic_ref", f"stale Semantic ref: {op.target_ref}"
                        )
                    superseded_semantic.append(semantic_target.statement)
                importance = min(
                    1.0, op.importance + (self._bonus if op.speaker_origin == "user" else 0)
                )
                tx.add_semantic(
                    SemanticRow(
                        id=str(uuid4()),
                        statement=op.statement,
                        persona_id=fragment.persona_id,
                        created_at=now,
                        updated_at=now,
                        importance=importance,
                        pinned=False,
                        source="extracted",
                        speaker_origin=op.speaker_origin,
                        valid_from=now,
                        provenance=list(provenance),
                        owner_user_id=fragment.owner_user_id,
                        memory_class="normal",
                        fact_key=None,
                        supersedes_id=semantic_target.id if semantic_target is not None else None,
                    )
                )
                added_semantic.append(op.statement)

            for row in episodic_rows:
                tx.add_episodic(row)

        return ExtractionResult(
            session_id=fragment.session_id,
            observation_id=observation_id,
            episodic_ids=episodic_ids,
            added_semantic=added_semantic,
            superseded_semantic=superseded_semantic,
            added_core=added_core,
            replaced_core=replaced_core,
            removed_core=removed_core,
        )


def _validate_output(output: ExtractionOutput, context: ExtractionContext) -> None:
    if output.validation_error is not None:
        raise ExtractionApplyError("invalid_extraction_output", output.validation_error)
    core_refs = context.core_ref_map
    semantic_refs = context.semantic_ref_map
    active_by_field: dict[str, list[CoreProfileEntry]] = {}
    for entry in core_refs.values():
        active_by_field.setdefault(entry.field_key, []).append(entry)

    touched_core_refs: set[str] = set()
    touched_single_fields: set[str] = set()
    pending_many: set[tuple[str, str]] = set()
    for update in output.core_updates:
        if not update.reason.strip():
            raise ExtractionApplyError("missing_core_reason", update.field)
        spec = CORE_FIELD_SPECS[update.field]
        if update.op == "add":
            if update.target_ref is not None or update.value is None:
                raise ExtractionApplyError("invalid_core_add", update.field)
            validate_core_value(update.field, update.value)
            if spec.cardinality == "single" and active_by_field.get(update.field):
                raise ExtractionApplyError("core_add_requires_replace", update.field)
            if spec.cardinality == "many":
                normalized = normalize_core_value(update.value)
                if any(
                    e.normalized_value == normalized for e in active_by_field.get(update.field, [])
                ):
                    raise ExtractionApplyError("duplicate_core_value", update.field)
                if (update.field, normalized) in pending_many:
                    raise ExtractionApplyError("duplicate_core_value", update.field)
                pending_many.add((update.field, normalized))
        else:
            if update.target_ref is None or update.target_ref not in core_refs:
                raise ExtractionApplyError("unknown_core_ref", update.target_ref or "")
            target = core_refs[update.target_ref]
            if target.field_key != update.field:
                raise ExtractionApplyError("core_field_mismatch", update.target_ref)
            if update.target_ref in touched_core_refs:
                raise ExtractionApplyError("duplicate_core_target", update.target_ref)
            touched_core_refs.add(update.target_ref)
            if update.op == "replace":
                if update.value is None:
                    raise ExtractionApplyError("missing_core_value", update.field)
                validate_core_value(update.field, update.value)
            elif update.value is not None:
                raise ExtractionApplyError("remove_has_value", update.field)
        if spec.cardinality == "single":
            if update.field in touched_single_fields:
                raise ExtractionApplyError("duplicate_core_field_op", update.field)
            touched_single_fields.add(update.field)

    touched_semantic: set[str] = set()
    for op in output.semantic_ops:
        if not op.reason.strip():
            raise ExtractionApplyError("missing_semantic_reason", op.statement)
        if op.op == "add":
            if op.target_ref is not None:
                raise ExtractionApplyError("add_has_semantic_ref", op.target_ref)
        else:
            if op.target_ref is None or op.target_ref not in semantic_refs:
                raise ExtractionApplyError("unknown_semantic_ref", op.target_ref or "")
            if op.target_ref in touched_semantic:
                raise ExtractionApplyError("duplicate_semantic_target", op.target_ref)
            touched_semantic.add(op.target_ref)


def _episodic_rows(
    output: ExtractionOutput, fragment: ConversationFragment, now: datetime
) -> list[EpisodicRow]:
    occurred_at = fragment.utterances[-1].ts if fragment.utterances else now
    source_ref = _fragment_source_ref(fragment)
    return [
        EpisodicRow(
            id=str(uuid4()),
            summary=entry,
            source_ref=source_ref,
            persona_id=fragment.persona_id,
            occurred_at=occurred_at,
            created_at=now,
            importance=0.5,
            owner_user_id=fragment.owner_user_id,
        )
        for entry in output.episodic_entries
    ]


def _observation_id(
    fragment: ConversationFragment, first_ref: str | None, last_ref: str | None
) -> str:
    raw = "\x1f".join(
        (fragment.owner_user_id, fragment.session_id, first_ref or "", last_ref or "")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fragment_source_ref(fragment: ConversationFragment) -> str:
    if not fragment.utterances:
        return f"{fragment.session_id}#"
    first = fragment.utterances[0].source_ref.partition("#")[2]
    last = fragment.utterances[-1].source_ref.partition("#")[2]
    return f"{fragment.session_id}#{first}..{last}"
