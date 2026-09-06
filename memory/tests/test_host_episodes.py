"""Confirmed host receipts persist once without extraction or false read claims."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import cast
from unittest.mock import Mock

import pytest
from memory.host_episodes import is_notification_episode_source

from memory import (
    CurrentSessionRecallScope,
    EmbeddingIndexManager,
    EpisodicRow,
    Extractor,
    HostEpisode,
    HostEpisodeConflict,
    KeywordRetrieval,
    Memory,
    MemorySearchService,
    Reconciler,
    SearchRequest,
    SqliteMemoryStore,
    StructuredMemorySource,
    is_notification_episode_id,
    notification_episode_id,
    notification_source_ref,
)

PRESENTED_AT = datetime(2026, 9, 6, 7, 20, tzinfo=UTC)
SUMMARY = "2026-09-06 15:20，我通过桌宠气泡提醒会话「整理照片」有新回复，实际显示：“回来看看吧。”"


def _episode(**changes: object) -> HostEpisode:
    return replace(
        HostEpisode("n1", "p1", PRESENTED_AT, SUMMARY),
        **changes,  # type: ignore[arg-type]
    )


def _row(**changes: object) -> EpisodicRow:
    return replace(
        EpisodicRow(
            id=notification_episode_id("n1"),
            source_ref=notification_source_ref("n1"),
            persona_id="p1",
            summary=SUMMARY,
            occurred_at=PRESENTED_AT,
            created_at=PRESENTED_AT + timedelta(seconds=5),
        ),
        **changes,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("wrapped", [False, True])
def test_identity_handles_row_and_search_evidence_ids(wrapped: bool) -> None:
    episode_id = notification_episode_id("run:123")
    value = f"episodic:{episode_id}" if wrapped else episode_id
    assert is_notification_episode_id(value)
    assert is_notification_episode_source(value, "notification:run:123")
    assert not is_notification_episode_source(value, "notification:other")
    assert not is_notification_episode_source(value, "s1#123")


@pytest.mark.parametrize(
    "value",
    ["", "notification-presented:", "episodic:episodic:notification-presented:n1", "uuid4"],
)
def test_identity_does_not_match_arbitrary_memory_ids(value: str) -> None:
    assert not is_notification_episode_id(value)


@pytest.mark.parametrize("notification_id", ["", " ", "a#b", "a\nb", "a\x00b", "n" * 257])
def test_host_episode_rejects_invalid_notification_identity(notification_id: str) -> None:
    with pytest.raises(ValueError, match="notification_id"):
        _episode(notification_id=notification_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": "  "},
        {"summary": "a\x00b"},
        {"owner_user_id": ""},
        {"persona_id": " "},
        {"occurred_at": PRESENTED_AT.replace(tzinfo=None)},
    ],
)
def test_host_episode_requires_owned_fact_and_unambiguous_time(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _episode(**changes)


def test_facade_writes_and_recalls_without_extractor_or_conversation(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.db")
    extractor = Mock(spec=Extractor)
    reconciler = Mock(spec=Reconciler)
    index = Mock(spec=EmbeddingIndexManager)
    memory = Memory(
        store,
        cast(Extractor, extractor),
        cast(Reconciler, reconciler),
        retrieval=KeywordRetrieval(store),
        index_manager=cast(EmbeddingIndexManager, index),
    )
    before_write = datetime.now(UTC)
    assert memory.record_host_episode(_episode()) == "inserted"
    first = store.episodic_by_ids([notification_episode_id("n1")])[notification_episode_id("n1")]
    assert memory.record_host_episode(_episode()) == "duplicate"
    assert store.episodic_by_ids([first.id])[first.id] == first
    assert first.summary == SUMMARY
    assert first.occurred_at == PRESENTED_AT
    assert first.source_ref == "notification:n1"
    assert before_write <= first.created_at <= datetime.now(UTC)
    assert memory.retrieve("照片", persona_id="p1").items[0].text == SUMMARY
    assert memory.retrieve("照片", persona_id="other").is_empty()
    assert memory.retrieve("照片", persona_id="p1", owner_user_id="other").is_empty()
    extractor.extract.assert_not_called()
    reconciler.apply.assert_not_called()
    assert index.request_backfill.call_count == 2
    assert store._conn.execute("SELECT count(*) FROM extraction_commit").fetchone()[0] == 0
    assert len(store.list_episodic(owner_user_id="local", persona_id="p1")) == 1
    memory.close()


def test_replayed_receipt_survives_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    first = SqliteMemoryStore(path)
    assert first.insert_episodic_once(_row()) == "inserted"
    first.close()
    reopened = SqliteMemoryStore(path)
    try:
        assert (
            reopened.insert_episodic_once(_row(created_at=PRESENTED_AT + timedelta(days=1)))
            == "duplicate"
        )
        hits = reopened.search_episodic("照片", owner_user_id="local", persona_id="p1", limit=10)
        assert len(hits) == 1
        assert hits[0][0].created_at == PRESENTED_AT + timedelta(seconds=5)
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": "新文案"},
        {"persona_id": "p2"},
        {"owner_user_id": "other"},
        {"source_ref": "notification:other"},
        {"occurred_at": PRESENTED_AT + timedelta(seconds=1)},
        {"importance": 1.0},
    ],
)
def test_conflicting_receipt_preserves_first_fact(changes: dict[str, object]) -> None:
    store = SqliteMemoryStore(":memory:")
    try:
        assert store.insert_episodic_once(_row()) == "inserted"
        with pytest.raises(HostEpisodeConflict, match="immutable input conflict"):
            store.insert_episodic_once(_row(**changes))
        assert not store._conn.in_transaction
        assert store.episodic_by_ids([_row().id])[_row().id] == _row()
        assert store.insert_episodic_once(_row()) == "duplicate"
    finally:
        store.close()


def test_deleted_episode_is_never_resurrected_or_reindexed() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_episodic(_row(deleted_at=PRESENTED_AT + timedelta(minutes=1)))
    index = Mock(spec=EmbeddingIndexManager)
    memory = Memory(
        store,
        cast(Extractor, Mock(spec=Extractor)),
        Reconciler(store),
        index_manager=cast(EmbeddingIndexManager, index),
    )
    assert memory.record_host_episode(_episode()) == "deleted"
    assert store.episodic_by_ids([_row().id])[_row().id].deleted_at is not None
    assert store.search_episodic("照片", owner_user_id="local", persona_id="p1", limit=10) == []
    index.request_backfill.assert_not_called()
    memory.close()


def test_fts_failure_rolls_back_primary_row_and_allows_retry() -> None:
    store = SqliteMemoryStore(":memory:")

    def deny_fts_insert(
        action: int,
        table: str | None,
        column: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_INSERT and table == "episodic_fts":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        store._conn.set_authorizer(deny_fts_insert)
        with pytest.raises(sqlite3.DatabaseError):
            store.insert_episodic_once(_row())
        store._conn.set_authorizer(None)
        assert not store._conn.in_transaction
        assert store.episodic_by_ids([_row().id]) == {}
        assert store.insert_episodic_once(_row()) == "inserted"
        hits = store.search_episodic("照片", owner_user_id="local", persona_id="p1", limit=10)
        assert len(hits) == 1
    finally:
        store._conn.set_authorizer(None)
        store.close()


def test_two_store_connections_cannot_insert_same_receipt_twice(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    stores = [SqliteMemoryStore(path), SqliteMemoryStore(path)]
    barrier = Barrier(2)

    def insert(store: SqliteMemoryStore) -> str:
        barrier.wait(timeout=5)
        return store.insert_episodic_once(_row())

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(insert, stores))
        assert sorted(results) == ["duplicate", "inserted"]
        assert len(stores[0].list_episodic(owner_user_id="local", persona_id="p1")) == 1
        assert (
            len(stores[0].search_episodic("照片", owner_user_id="local", persona_id="p1", limit=10))
            == 1
        )
    finally:
        for store in stores:
            store.close()


def test_scope_recognizes_host_fact_without_fabricated_session_event() -> None:
    store = SqliteMemoryStore(":memory:")
    try:
        store.insert_episodic_once(_row())
        store.add_episodic(_row(id="conversation", source_ref="s1#u1"))
        service = MemorySearchService([StructuredMemorySource(store)])
        result = service.search(
            SearchRequest(
                text="照片",
                persona_id="p1",
                owner_user_id="local",
                mode="automatic",
                session_id="s1",
                automatic_recall_scope=CurrentSessionRecallScope("s1", None),
            )
        )
        reasons = {item.evidence_id: item.reason for item in result.scope_decisions}
        assert reasons[f"episodic:{_row().id}"] == "host_notification_event"
        assert reasons["episodic:conversation"] == "current_session_empty_boundary"
        assert [item.evidence_id for item in result.evidence] == [f"episodic:{_row().id}"]
        assert (
            store.soft_delete_by_source_events(
                session_id="s1", event_uuids={"u1"}, deleted_at=PRESENTED_AT
            )["episodic"]
            == 1
        )
        assert store.episodic_by_ids([_row().id])[_row().id].deleted_at is None
    finally:
        store.close()


def test_host_like_id_does_not_bypass_actual_conversation_scope() -> None:
    store = SqliteMemoryStore(":memory:")
    try:
        store.add_episodic(_row(source_ref="s1#u1"))
        result = MemorySearchService([StructuredMemorySource(store)]).search(
            SearchRequest(
                text="照片",
                persona_id="p1",
                owner_user_id="local",
                mode="automatic",
                session_id="s1",
                automatic_recall_scope=CurrentSessionRecallScope("s1", None),
            )
        )
        assert result.evidence == ()
        assert result.scope_decisions[0].reason == "current_session_empty_boundary"
    finally:
        store.close()
