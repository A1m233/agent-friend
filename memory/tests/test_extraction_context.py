from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memory.extraction.context import ExtractionContextBuilder
from memory.extraction.extractor import _pack_semantic_update_turns

from memory import (
    ConversationFragment,
    SemanticRow,
    SemanticUpdateTurn,
    SqliteMemoryStore,
    Utterance,
)


def _fragment(query: str) -> ConversationFragment:
    return ConversationFragment(
        session_id="s1",
        persona_id="p1",
        utterances=[
            Utterance(
                speaker="user",
                text=query,
                ts=datetime(2026, 8, 15, tzinfo=UTC),
                source_ref="s1#u1",
            )
        ],
    )


def _row(row_id: str, statement: str, updated_at: datetime) -> SemanticRow:
    return SemanticRow(
        id=row_id,
        statement=statement,
        persona_id="p1",
        created_at=updated_at,
        updated_at=updated_at,
        valid_from=updated_at,
    )


def test_context_prioritizes_fts_then_recent_and_deduplicates() -> None:
    store = SqliteMemoryStore(":memory:")
    now = datetime(2026, 8, 15, tzinfo=UTC)
    store.add_semantic(_row("relevant-old", "用户喜欢攀岩", now - timedelta(days=10)))
    store.add_semantic(_row("recent", "用户最近开始学钢琴", now))

    context = ExtractionContextBuilder(store, semantic_token_budget=100).build(_fragment("攀岩"))

    assert [row.id for _, row in context.semantic_facts] == ["relevant-old", "recent"]
    assert [ref for ref, _ in context.semantic_facts] == ["M001", "M002"]


def test_context_budget_applies_only_to_rendered_ordinary_semantic() -> None:
    store = SqliteMemoryStore(":memory:")
    now = datetime(2026, 8, 15, tzinfo=UTC)
    store.add_semantic(_row("one", "一" * 40, now))
    store.add_semantic(_row("two", "二" * 40, now - timedelta(seconds=1)))

    context = ExtractionContextBuilder(store, semantic_token_budget=25).build(_fragment("无匹配"))

    assert len(context.semantic_facts) == 1
    assert context.semantic_total == 2
    assert context.semantic_omitted == 1
    assert context.semantic_estimated_tokens <= 25


def _semantic_update_turn(index: int, *, chars: int) -> SemanticUpdateTurn:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    return SemanticUpdateTurn(
        utterances=(
            Utterance(
                speaker="user",
                text=f"问题 {index} " + "问" * chars,
                ts=now,
                source_ref=f"s1#u{index}",
            ),
            Utterance(
                speaker="agent",
                text=f"回答 {index}",
                ts=now,
                source_ref=f"s1#a{index}",
            ),
        )
    )


def test_semantic_update_capacity_keeps_contiguous_recent_turn_suffix() -> None:
    turns = tuple(_semantic_update_turn(index, chars=24_000) for index in range(1, 8))

    packed = _pack_semantic_update_turns(
        turns,
        context_window=256 * 1024,
        mandatory_tokens=0,
    )

    assert 0 < len(packed.turns) < len(turns)
    assert packed.turns == turns[-len(packed.turns) :]
    assert packed.omitted_turns == len(turns) - len(packed.turns)


def test_latest_oversized_soft_budget_turn_is_kept_when_it_fits_hard_budget() -> None:
    older = _semantic_update_turn(1, chars=10)
    latest = _semantic_update_turn(2, chars=130_000)

    packed = _pack_semantic_update_turns(
        (older, latest),
        context_window=256 * 1024,
        mandatory_tokens=0,
    )

    assert packed.turns == (latest,)
    assert packed.omitted_turns == 1


def test_latest_turn_over_hard_budget_skips_reconciliation_context() -> None:
    latest = _semantic_update_turn(1, chars=3_000)

    packed = _pack_semantic_update_turns(
        (latest,),
        context_window=10_000,
        mandatory_tokens=7_000,
    )

    assert packed.turns == ()
    assert packed.omitted_turns == 1
    assert packed.fallback_reason == "latest_turn_exceeds_hard_budget"
