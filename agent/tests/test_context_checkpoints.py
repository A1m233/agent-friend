"""Checkpoint identity domains, integrity, and conservative legacy recovery."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from agent.context.checkpoints import (
    bind_business_boundary,
    checkpoint_prefix_digest,
    resolve_prior_summary,
    summary_message,
)
from agent.context.protocol import CompactionRecord
from agent.messages import Message
from agent.sessions import Event


def _event(kind: str, uuid: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(type=kind, uuid=uuid, ts=datetime.now(UTC), payload=payload or {})  # type: ignore[arg-type]


def _fixture() -> tuple[list[Event], list[Message], CompactionRecord]:
    events = [_event("user_message", "u1"), _event("assistant_message", "a1")]
    history = [
        Message(role="user", content="first", uuid="u1"),
        Message(role="assistant", content="answer", uuid="a1"),
        Message(role="system", content="interrupted", uuid="model-aborted"),
        Message(role="user", content="expired", uuid="model-scope-expired"),
        Message(role="user", content="next", uuid="model-user"),
    ]
    record = CompactionRecord(
        summary="facts",
        checkpoint_version=3,
        scope="model",
        covered_through_uuid="model-aborted",
        summary_source_from_uuid="u1",
        retained_from_uuid="model-scope-expired",
        summary_message_uuid="summary-item",
        model_prefix_sha256=checkpoint_prefix_digest(message.uuid for message in history[:3]),
        activity_id="checkpoint",
    )
    return events, history, record


def test_model_checkpoint_does_not_use_business_projection_or_leak_its_ids() -> None:
    events, history, record = _fixture()
    events.append(_event("compaction", record.activity_id, asdict(record)))
    resolution = resolve_prior_summary(events, history, scope="model")
    assert resolution.status == "valid"
    assert resolution.prior_summary is not None
    assert resolution.prior_summary.materialized is False
    assert resolve_prior_summary(events, history[:2], scope="model").prior_summary is None
    assert resolve_prior_summary(events, history, scope="business").prior_summary is None


def test_business_anchor_is_separate_and_invalidated_by_prefix_edit() -> None:
    events, history, record = _fixture()
    record = bind_business_boundary(record, history, events)
    assert record.business_covered_through_uuid == "a1"
    checkpoint = _event("compaction", record.activity_id, asdict(record))
    resolution = resolve_prior_summary([*events, checkpoint], history[:2], scope="business")
    assert resolution.prior_summary is not None
    assert resolution.prior_summary.covered_through_uuid == "a1"
    rewritten = [_event("user_message", "replacement"), events[1], checkpoint]
    assert resolve_prior_summary(rewritten, history, scope="business").prior_summary is None


def test_materialized_checkpoint_requires_durable_identity_and_tail() -> None:
    events, history, record = _fixture()
    events.append(_event("compaction", record.activity_id, asdict(record)))
    item = summary_message(record.summary, record.summary_message_uuid, record.activity_id)
    folded = [item, *history[3:]]
    resolution = resolve_prior_summary(events, folded, scope="model")
    assert resolution.status == "materialized"
    assert resolution.prior_summary is not None
    assert resolution.prior_summary.materialized is True
    for invalid in (
        [replace(item, uuid="unmarked"), *history[3:]],
        [replace(item, meta={}), *history[3:]],
        [replace(item, content="changed"), *history[3:]],
        [item, history[-1]],
        [item, *history],
    ):
        assert resolve_prior_summary(events, invalid, scope="model").prior_summary is None


def test_model_prefix_edit_is_rejected_even_if_all_three_boundaries_remain() -> None:
    events, history, record = _fixture()
    events.append(_event("compaction", record.activity_id, asdict(record)))
    history[1] = replace(history[1], uuid="replacement")
    result = resolve_prior_summary(events, history, scope="model")
    assert result.invalid_reason == "model_prefix_mismatch"


def test_materialized_summary_allows_catalog_retained_by_context_policy() -> None:
    events, history, record = _fixture()
    history[2].meta["synthetic"] = "skill_catalog_state"
    events.append(_event("compaction", record.activity_id, asdict(record)))
    item = summary_message(record.summary, record.summary_message_uuid, record.activity_id)
    assert (
        resolve_prior_summary(events, [item, *history[2:]], scope="model").status == "materialized"
    )


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_model_boundaries_require_actual_history(version: int) -> None:
    events, history, record = _fixture()
    payload = asdict(record)
    payload["checkpoint_version"] = version
    payload.pop("summary_message_uuid")
    events.append(_event("compaction", record.activity_id, payload))
    assert resolve_prior_summary(events, history, scope="model").prior_summary is not None
    assert resolve_prior_summary(events, history[:2]).prior_summary is None
    same_text = summary_message(record.summary, "unmarked", "")
    assert (
        resolve_prior_summary(events, [same_text, *history[3:]], scope="model").prior_summary
        is None
    )


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_business_checkpoints_remain_compatible(version: int) -> None:
    events, history, record = _fixture()
    payload = asdict(replace(record, covered_through_uuid="a1", retained_from_uuid="model-aborted"))
    payload["checkpoint_version"] = version
    events.append(_event("compaction", record.activity_id, payload))
    result = resolve_prior_summary(events, history)
    assert result.prior_summary is not None
    assert result.prior_summary.covered_through_uuid == "a1"
