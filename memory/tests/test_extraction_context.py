from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memory.extraction.context import ExtractionContextBuilder

from memory import ConversationFragment, SemanticRow, SqliteMemoryStore, Utterance


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
