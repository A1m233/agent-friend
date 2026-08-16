"""Typed extraction proposals and observable commit results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..core_profile import CoreFieldKey

__all__ = [
    "CoreUpdate",
    "ExtractionAttempt",
    "ExtractionAttemptStatus",
    "ExtractionOutput",
    "ExtractionResult",
    "SemanticChange",
    "SemanticFactDraft",
    "SemanticOp",
    "SemanticRetirement",
]

ExtractionAttemptStatus = Literal[
    "success",
    "empty_response",
    "invalid_json",
    "invalid_shape",
    "empty_output",
    "llm_error",
]


@dataclass(frozen=True)
class CoreUpdate:
    """One schema-bound Core Profile proposal.

    ``replace`` and ``remove`` must address an entry shown to the extractor by its
    short ``Cxxx`` reference.  ``add`` never accepts a target reference.
    """

    op: Literal["add", "replace", "remove"]
    field: CoreFieldKey
    reason: str
    value: str | None = None
    target_ref: str | None = None


@dataclass(frozen=True)
class SemanticOp:
    """Legacy v1 ordinary Semantic proposal.

    New extractor output uses :class:`SemanticChange`.  This contract remains so
    stored eval fixtures and callers can be converted at the parser boundary.
    """

    op: Literal["add", "supersede"]
    statement: str
    reason: str
    importance: float = 0.5
    speaker_origin: Literal["user", "agent"] = "user"
    target_ref: str | None = None


@dataclass(frozen=True)
class SemanticRetirement:
    """One visible Semantic fact that must stop being current."""

    target_ref: str
    reason: str


@dataclass(frozen=True)
class SemanticFactDraft:
    """One new ordinary Semantic fact introduced by a conceptual change."""

    statement: str
    reason: str
    importance: float = 0.5
    speaker_origin: Literal["user", "agent"] = "user"


@dataclass(frozen=True)
class SemanticChange:
    """One atomic conceptual change with arbitrary old and new members."""

    retirements: list[SemanticRetirement] = field(default_factory=list)
    new_facts: list[SemanticFactDraft] = field(default_factory=list)


@dataclass(frozen=True)
class ExtractionOutput:
    """Parsed extractor output, ordered by conceptual responsibility."""

    core_updates: list[CoreUpdate] = field(default_factory=list)
    semantic_changes: list[SemanticChange] = field(default_factory=list)
    episodic_entries: list[str] = field(default_factory=list)
    validation_error: str | None = None
    output_contract_version: int = 2
    # Programmatic compatibility for old fixtures. Parser-produced v1 payloads
    # are converted before constructing ExtractionOutput.
    semantic_ops: list[SemanticOp] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.semantic_ops:
            return
        if self.semantic_changes:
            object.__setattr__(self, "validation_error", "ambiguous_semantic_contract")
            return
        if any(
            (op.op == "add" and op.target_ref is not None)
            or (op.op == "supersede" and not op.target_ref)
            for op in self.semantic_ops
        ):
            object.__setattr__(self, "validation_error", "invalid_legacy_semantic_op")
        changes = [_legacy_op_to_change(op) for op in self.semantic_ops]
        object.__setattr__(self, "semantic_changes", changes)
        object.__setattr__(self, "output_contract_version", 1)

    def is_noop(self) -> bool:
        return not self.core_updates and not self.semantic_changes and not self.episodic_entries


@dataclass(frozen=True)
class ExtractionAttempt:
    """Diagnostic snapshot for one LLM extraction attempt."""

    session_id: str
    first_source_ref: str | None
    last_source_ref: str | None
    utterance_count: int
    existing_fact_count: int
    status: ExtractionAttemptStatus
    duration_ms: float
    core_context_count: int = 0
    semantic_total: int = 0
    semantic_omitted: int = 0
    core_update_count: int = 0
    episodic_count: int = 0
    semantic_count: int = 0
    output_contract_version: int | None = None
    raw_response: str | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """The atomic store result for one observed fragment."""

    session_id: str
    observation_id: str = ""
    committed: bool = True
    duplicate: bool = False
    episodic_ids: list[str] = field(default_factory=list)
    semantic_change_ids: list[str] = field(default_factory=list)
    introduced_semantic: list[str] = field(default_factory=list)
    retired_semantic: list[str] = field(default_factory=list)
    output_contract_version: int | None = None
    # Compatibility names used by the CLI and existing integrations.
    added_semantic: list[str] = field(default_factory=list)
    superseded_semantic: list[str] = field(default_factory=list)
    added_core: list[str] = field(default_factory=list)
    replaced_core: list[str] = field(default_factory=list)
    removed_core: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.episodic_ids,
                self.semantic_change_ids,
                self.introduced_semantic,
                self.retired_semantic,
                self.added_semantic,
                self.superseded_semantic,
                self.added_core,
                self.replaced_core,
                self.removed_core,
            )
        )


def _legacy_op_to_change(op: SemanticOp) -> SemanticChange:
    retirement = (
        [SemanticRetirement(target_ref=op.target_ref, reason=op.reason)]
        if op.op == "supersede" and op.target_ref
        else []
    )
    return SemanticChange(
        retirements=retirement,
        new_facts=[
            SemanticFactDraft(
                statement=op.statement,
                reason=op.reason,
                importance=op.importance,
                speaker_origin=op.speaker_origin,
            )
        ],
    )
