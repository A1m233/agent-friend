"""Pure resolution of persisted context-compaction checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .protocol import PriorSummary

if TYPE_CHECKING:
    from ..messages import Message
    from ..sessions import Event

CheckpointStatus = Literal["none", "valid", "legacy_revalidated", "invalid"]


@dataclass(frozen=True)
class PriorSummaryResolution:
    """One checkpoint validation result over a frozen active event sequence."""

    prior_summary: PriorSummary | None
    status: CheckpointStatus
    invalid_reason: str = ""


def resolve_prior_summary(
    events: list[Event],
    messages: list[Message],
) -> PriorSummaryResolution:
    """Resolve the latest valid compaction with Conversation's existing rules."""

    comp: Event | None = None
    for event in reversed(events):
        if event.type == "compaction":
            comp = event
            break
    if comp is None:
        return PriorSummaryResolution(prior_summary=None, status="none")

    summary = comp.payload.get("summary", "")
    covered = comp.payload.get("covered_through_uuid", "")
    if not isinstance(summary, str) or not summary or not isinstance(covered, str) or not covered:
        return PriorSummaryResolution(
            prior_summary=None,
            status="invalid",
            invalid_reason="missing_summary_or_covered_boundary",
        )

    indices = {message.uuid: index for index, message in enumerate(messages)}
    covered_index = indices.get(covered)
    if covered_index is None:
        return PriorSummaryResolution(
            prior_summary=None,
            status="invalid",
            invalid_reason="covered_boundary_not_active",
        )

    checkpoint_version = int(comp.payload.get("checkpoint_version", 1))
    source_from = str(comp.payload.get("summary_source_from_uuid", ""))
    retained_from = str(comp.payload.get("retained_from_uuid", ""))
    if checkpoint_version >= 2:
        source_index = indices.get(source_from)
        retained_index = indices.get(retained_from) if retained_from else len(messages)
        if (
            source_index is None
            or retained_index is None
            or source_index > covered_index
            or retained_index != covered_index + 1
        ):
            return PriorSummaryResolution(
                prior_summary=None,
                status="invalid",
                invalid_reason="checkpoint_boundaries_not_contiguous",
            )

    return PriorSummaryResolution(
        prior_summary=PriorSummary(
            summary=summary,
            covered_through_uuid=covered,
            checkpoint_version=checkpoint_version,
            summary_prompt_version=int(comp.payload.get("summary_prompt_version", 0)),
            summary_source_from_uuid=source_from,
            retained_from_uuid=retained_from,
            omitted_before_count=int(comp.payload.get("omitted_before_count", 0)),
        ),
        status="valid" if checkpoint_version >= 2 else "legacy_revalidated",
    )


__all__ = [
    "CheckpointStatus",
    "PriorSummaryResolution",
    "resolve_prior_summary",
]
