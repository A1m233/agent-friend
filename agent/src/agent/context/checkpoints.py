"""Validate compaction checkpoints in their declared message identity domain."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from ..attachments import AttachmentRef
from ..message_references import parse_quoted_text_references
from ..messages import Message
from ..request_context.source import request_source_meta
from .protocol import CompactionRecord, PriorSummary
from .summary_prompt import render_summary_as_context
from .task_anchor import (
    TASK_ANCHOR_VERSION,
    anchor_content_digest,
    task_anchor_message,
    user_source_digest,
)

if TYPE_CHECKING:
    from ..sessions import Event

CheckpointStatus = Literal["none", "valid", "materialized", "legacy_revalidated", "invalid"]


@dataclass(frozen=True)
class PriorSummaryResolution:
    """One checkpoint validation result over frozen events and message history."""

    prior_summary: PriorSummary | None
    status: CheckpointStatus
    invalid_reason: str = ""


def checkpoint_prefix_digest(uuids: Iterable[str]) -> str:
    """Bind a boundary to its ordered immutable identities, including prior edits."""
    return sha256(json.dumps(list(uuids), separators=(",", ":")).encode()).hexdigest()


def summary_message(summary: str, message_uuid: str, checkpoint_id: str) -> Message:
    """A durable model item; its identity survives folding and rollout restoration."""
    return Message(
        role="system",
        content=render_summary_as_context(summary),
        uuid=message_uuid,
        meta={"synthetic": "context_summary", "compaction_id": checkpoint_id},
    )


def compaction_payload(record: CompactionRecord) -> dict[str, object]:
    """Keep checkpoint serialization in sync with its versioned contract."""
    payload = asdict(record)
    payload.pop("activity_id", None)
    return payload


def _restore_task_anchor(comp: Event, events: list[Event]) -> Message | None:
    payload = comp.payload
    if payload.get("task_anchor_version") != TASK_ANCHOR_VERSION:
        return None
    source_uuid = payload.get("source_user_event_uuid")
    source = next(
        (event for event in events if event.uuid == source_uuid and event.type == "user_message"),
        None,
    )
    anchor_uuid = payload.get("task_anchor_uuid")
    if (
        source is None
        or not isinstance(source.payload.get("content", ""), str)
        or not isinstance(anchor_uuid, str)
        or not anchor_uuid
        or anchor_uuid == source_uuid
        or user_source_digest(source.payload) != payload.get("source_user_sha256")
    ):
        return None
    try:
        user = Message(
            role="user",
            content=source.payload.get("content", ""),
            uuid=source.uuid,
            meta=request_source_meta(source.payload.get("request_source")),
            attachments=[
                AttachmentRef.from_dict(item) for item in source.payload.get("attachments", [])
            ],
            references=parse_quoted_text_references(source.payload.get("references", [])),
        )
        anchor = replace(task_anchor_message(user), uuid=anchor_uuid)
    except (TypeError, ValueError, KeyError):
        return None
    if anchor_content_digest(anchor) != payload.get("task_anchor_sha256"):
        return None
    return anchor


def bind_business_boundary(
    record: CompactionRecord, history: list[Message], events: list[Event]
) -> CompactionRecord:
    """Keep only a business identity proven to precede the model cutoff.

    A conservative earlier boundary retains extra raw context rather than inventing
    an event mapping for model-only messages.
    """
    cut = next(
        (
            index
            for index, message in enumerate(history)
            if message.uuid == record.covered_through_uuid
        ),
        None,
    )
    if cut is None:
        return record
    eligible = {
        event.uuid: index
        for index, event in enumerate(events)
        if event.type in {"user_message", "assistant_message", "tool_call_result"}
    }
    candidates = [eligible[item.uuid] for item in history[: cut + 1] if item.uuid in eligible]
    if not candidates:
        return record
    index = max(candidates)
    if any(eligible[item.uuid] <= index for item in history[cut + 1 :] if item.uuid in eligible):
        return record
    return replace(
        record,
        business_covered_through_uuid=events[index].uuid,
        business_prefix_sha256=checkpoint_prefix_digest(
            event.uuid for event in events[: index + 1]
        ),
    )


def _invalid(reason: str) -> PriorSummaryResolution:
    return PriorSummaryResolution(None, "invalid", reason)


def _business_boundary(comp: Event, events: list[Event]) -> str:
    covered = comp.payload.get("business_covered_through_uuid")
    if not isinstance(covered, str) or not covered:
        return ""
    index = next((index for index, event in enumerate(events) if event.uuid == covered), None)
    if index is None or events[index].type not in {
        "user_message",
        "assistant_message",
        "tool_call_result",
    }:
        return ""
    digest = checkpoint_prefix_digest(event.uuid for event in events[: index + 1])
    return covered if digest == comp.payload.get("business_prefix_sha256") else ""


def resolve_prior_summary(
    events: list[Event],
    messages: list[Message],
    *,
    scope: Literal["business", "model"] = "business",
) -> PriorSummaryResolution:
    """Resolve without crossing identity domains or guessing legacy summary matches."""
    comp = next((event for event in reversed(events) if event.type == "compaction"), None)
    if comp is None:
        return PriorSummaryResolution(None, "none")
    summary = comp.payload.get("summary", "")
    covered = comp.payload.get("covered_through_uuid", "")
    if not isinstance(summary, str) or not summary or not isinstance(covered, str) or not covered:
        return _invalid("missing_summary_or_covered_boundary")
    try:
        version = int(comp.payload.get("checkpoint_version", 1))
        prompt_version = int(comp.payload.get("summary_prompt_version", 0))
        omitted = int(comp.payload.get("omitted_before_count", 0))
    except (TypeError, ValueError):
        return _invalid("invalid_checkpoint_version")
    if version not in {1, 2, 3, 4}:
        return _invalid("unsupported_checkpoint_version")
    source = str(comp.payload.get("summary_source_from_uuid", ""))
    retained = str(comp.payload.get("retained_from_uuid", ""))
    business_covered = ""
    message_uuid = ""
    materialized = False
    task_anchor = _restore_task_anchor(comp, events) if version == 4 else None
    if version == 4 and task_anchor is None:
        return _invalid("task_anchor_source_mismatch")
    indices = {message.uuid: index for index, message in enumerate(messages)}
    if len(indices) != len(messages):
        return _invalid("duplicate_message_identities")
    if version >= 3:
        if comp.payload.get("scope") != "model":
            return _invalid("invalid_checkpoint_scope")
        business_covered = _business_boundary(comp, events)
        if comp.payload.get("business_covered_through_uuid") and not business_covered:
            return _invalid("business_prefix_mismatch")
        if scope == "business":
            if not business_covered:
                return _invalid("business_boundary_not_verified")
            return PriorSummaryResolution(
                PriorSummary(summary, business_covered, checkpoint_version=version), "valid"
            )
        message_uuid = str(comp.payload.get("summary_message_uuid", ""))
        if not message_uuid:
            return _invalid("missing_summary_identity")
        summary_index = indices.get(message_uuid)
        if summary_index is not None:
            item = messages[summary_index]
            covered_item = messages[indices[covered]] if covered in indices else None
            retained_index = indices.get(retained) if retained else len(messages)
            if (
                item.role != "system"
                or item.meta.get("synthetic") != "context_summary"
                or item.meta.get("compaction_id") != comp.uuid
                or item.content != render_summary_as_context(summary)
                # The context policy deliberately retains the latest skill catalog,
                # even if it was also the final item in the compacted prefix.
                or (
                    covered_item is not None
                    and covered_item.meta.get("synthetic") != "skill_catalog_state"
                )
                or retained_index is None
                or retained_index <= summary_index
            ):
                return _invalid("materialized_summary_mismatch")
            materialized = True
            if task_anchor is not None:
                anchor_index = indices.get(task_anchor.uuid)
                if (
                    anchor_index is None
                    or anchor_index != summary_index + 1
                    or retained_index <= anchor_index
                    or str(comp.payload.get("source_user_event_uuid")) in indices
                ):
                    return _invalid("materialized_task_anchor_mismatch")
                actual = messages[anchor_index]
                if (
                    actual.role != task_anchor.role
                    or actual.content != task_anchor.content
                    or any(actual.meta.get(key) != value for key, value in task_anchor.meta.items())
                ):
                    return _invalid("materialized_task_anchor_mismatch")
    if not materialized:
        covered_index = indices.get(covered)
        if covered_index is None:
            return _invalid("covered_boundary_not_active")
        if version >= 2:
            source_index = indices.get(source)
            retained_index = indices.get(retained) if retained else len(messages)
            if (
                source_index is None
                or retained_index is None
                or source_index > covered_index
                # No retained tail describes the original candidate, not the
                # absence of later accepted messages after a cold restart.
                or (bool(retained) and retained_index != covered_index + 1)
            ):
                return _invalid("checkpoint_boundaries_not_contiguous")
        if version >= 3:
            digest = checkpoint_prefix_digest(
                message.uuid for message in messages[: covered_index + 1]
            )
            if digest != comp.payload.get("model_prefix_sha256"):
                return _invalid("model_prefix_mismatch")
    if version < 3:
        business_covered = covered if any(event.uuid == covered for event in events) else ""
    return PriorSummaryResolution(
        PriorSummary(
            summary=summary,
            covered_through_uuid=covered,
            checkpoint_version=version,
            summary_prompt_version=prompt_version,
            summary_source_from_uuid=source,
            retained_from_uuid=retained,
            omitted_before_count=omitted,
            scope=scope,
            summary_message_uuid=message_uuid,
            checkpoint_id=comp.uuid,
            materialized=materialized,
            business_covered_through_uuid=business_covered,
            task_anchor_uuid=task_anchor.uuid if task_anchor else "",
            source_user_event_uuid=str(comp.payload.get("source_user_event_uuid", "")),
            source_user_sha256=str(comp.payload.get("source_user_sha256", "")),
            task_anchor_sha256=str(comp.payload.get("task_anchor_sha256", "")),
            task_anchor_version=TASK_ANCHOR_VERSION if task_anchor else 0,
            task_anchor=task_anchor,
        ),
        "materialized" if materialized else "legacy_revalidated" if version == 1 else "valid",
    )


__all__ = ["CheckpointStatus", "PriorSummaryResolution", "resolve_prior_summary"]
