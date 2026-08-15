"""Build bounded, reference-addressable context for one extraction call."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core_profile import CoreProfileEntry

if TYPE_CHECKING:
    from ..contracts import ConversationFragment
    from ..store import SemanticRow, SqliteMemoryStore

SEMANTIC_CONTEXT_TOKEN_BUDGET = 50_000
_CHARS_TO_TOKENS = 0.5


@dataclass(frozen=True)
class ExtractionContext:
    core_entries: tuple[tuple[str, CoreProfileEntry], ...] = ()
    semantic_facts: tuple[tuple[str, SemanticRow], ...] = ()
    semantic_total: int = 0
    semantic_omitted: int = 0
    semantic_estimated_tokens: int = 0

    @property
    def core_ref_map(self) -> dict[str, CoreProfileEntry]:
        return dict(self.core_entries)

    @property
    def semantic_ref_map(self) -> dict[str, SemanticRow]:
        return dict(self.semantic_facts)

    @classmethod
    def from_existing(cls, facts: list[SemanticRow]) -> ExtractionContext:
        selected, tokens = _pack_semantic(facts, SEMANTIC_CONTEXT_TOKEN_BUDGET)
        return cls(
            semantic_facts=tuple(
                (f"M{index:03d}", fact) for index, fact in enumerate(selected, start=1)
            ),
            semantic_total=len(facts),
            semantic_omitted=len(facts) - len(selected),
            semantic_estimated_tokens=tokens,
        )


class ExtractionContextBuilder:
    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        semantic_token_budget: int = SEMANTIC_CONTEXT_TOKEN_BUDGET,
    ) -> None:
        if semantic_token_budget <= 0:
            raise ValueError("semantic_token_budget must be positive")
        self._store = store
        self._budget = semantic_token_budget

    def build(self, fragment: ConversationFragment) -> ExtractionContext:
        core = list(
            self._store.core_profile(
                owner_user_id=fragment.owner_user_id,
                persona_id=fragment.persona_id,
            ).entries
        )
        all_semantic = self._store.list_active_ordinary_semantic(
            owner_user_id=fragment.owner_user_id
        )
        query = "\n".join(
            utterance.text for utterance in fragment.utterances if utterance.speaker == "user"
        ).strip()
        if not query:
            query = "\n".join(utterance.text for utterance in fragment.utterances).strip()
        related = self._store.search_semantic(
            query,
            owner_user_id=fragment.owner_user_id,
            persona_id=fragment.persona_id,
            limit=max(1, len(all_semantic)),
        )
        ordered: list[SemanticRow] = []
        seen: set[str] = set()
        for fact in [*(row for row, _ in related), *all_semantic]:
            if fact.id in seen:
                continue
            seen.add(fact.id)
            ordered.append(fact)
        selected, estimated_tokens = _pack_semantic(ordered, self._budget)
        return ExtractionContext(
            core_entries=tuple(
                (f"C{index:03d}", entry) for index, entry in enumerate(core, start=1)
            ),
            semantic_facts=tuple(
                (f"M{index:03d}", fact) for index, fact in enumerate(selected, start=1)
            ),
            semantic_total=len(ordered),
            semantic_omitted=len(ordered) - len(selected),
            semantic_estimated_tokens=estimated_tokens,
        )


def estimate_semantic_line_tokens(ref: str, statement: str) -> int:
    return max(1, math.ceil(len(f"[{ref}] {statement}") * _CHARS_TO_TOKENS))


def _pack_semantic(facts: list[SemanticRow], token_budget: int) -> tuple[list[SemanticRow], int]:
    selected: list[SemanticRow] = []
    used = 0
    for fact in facts:
        provisional_ref = f"M{len(selected) + 1:03d}"
        cost = estimate_semantic_line_tokens(provisional_ref, fact.statement)
        if used + cost > token_budget:
            continue
        selected.append(fact)
        used += cost
    return selected, used
