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
    "SemanticOp",
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
    """One ordinary Semantic proposal using an exact ``Mxxx`` reference."""

    op: Literal["add", "supersede"]
    statement: str
    reason: str
    importance: float = 0.5
    speaker_origin: Literal["user", "agent"] = "user"
    target_ref: str | None = None


@dataclass(frozen=True)
class ExtractionOutput:
    """Parsed extractor output, ordered by conceptual responsibility."""

    core_updates: list[CoreUpdate] = field(default_factory=list)
    semantic_ops: list[SemanticOp] = field(default_factory=list)
    episodic_entries: list[str] = field(default_factory=list)
    validation_error: str | None = None

    def is_noop(self) -> bool:
        return not self.core_updates and not self.semantic_ops and not self.episodic_entries


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
                self.added_semantic,
                self.superseded_semantic,
                self.added_core,
                self.replaced_core,
                self.removed_core,
            )
        )
