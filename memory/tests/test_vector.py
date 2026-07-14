"""M48.3 派生向量索引 generation / manifest 生命周期测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from memory.search import (
    EmbeddingIndexManager,
    SqliteVecIndex,
    VectorFilters,
    VectorHit,
    VectorRecord,
)
from memory.store import SemanticRow, SqliteMemoryStore


class FakeProvider:
    model_id = "fake-v1"
    dimension = 2

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("embedding failed")
        return [[float(len(text)), 1.0] for text in texts]


class FakeIndex:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], VectorRecord] = {}

    def upsert(self, generation_id: str, records: list[VectorRecord]) -> None:
        for record in records:
            self.records[generation_id, record.evidence_id] = record

    def search(
        self,
        vector: list[float],
        *,
        filters: VectorFilters,
        limit: int,
    ) -> list[VectorHit]:
        del vector
        ids = [
            evidence_id
            for (generation_id, evidence_id), record in self.records.items()
            if generation_id == filters.generation_id
            and record.owner_user_id == filters.owner_user_id
        ]
        return [VectorHit(evidence_id=item, distance=0.1) for item in ids[:limit]]

    def delete(self, generation_id: str, evidence_ids: list[str]) -> None:
        for evidence_id in evidence_ids:
            self.records.pop((generation_id, evidence_id), None)

    def close(self) -> None:
        pass


def _row(row_id: str, text: str) -> SemanticRow:
    now = datetime.now(UTC)
    return SemanticRow(
        id=row_id,
        statement=text,
        persona_id="p1",
        created_at=now,
        updated_at=now,
    )


def test_backfill_is_idempotent_and_activates_complete_generation() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_row("one", "第一条"))
    provider = FakeProvider()
    index = FakeIndex()
    manager = EmbeddingIndexManager(store, provider, index, batch_size=1)

    first = manager.backfill_once()
    second = manager.backfill_once()

    assert first.status == "ready"
    assert first.active_generation == manager.generation_id
    assert first.indexed_count == first.total_count == 1
    assert second.status == "ready"
    assert provider.calls == 1
    assert manager.search("第一条", owner_user_id="local", limit=2)[0].evidence_id == "semantic:one"
    manager.close()
    store.close()


def test_failed_backfill_keeps_keyword_path_and_resumes_from_manifest() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_row("one", "第一条"))
    store.add_semantic(_row("two", "第二条"))
    provider = FakeProvider(fail_after=1)
    index = FakeIndex()
    manager = EmbeddingIndexManager(store, provider, index, batch_size=1)

    failed = manager.backfill_once()
    assert failed.status == "failed"
    assert failed.indexed_count == 1
    assert manager.search("查询", owner_user_id="local", limit=2) == []

    provider.fail_after = None
    ready = manager.backfill_once()
    assert ready.status == "ready"
    assert ready.indexed_count == 2
    assert provider.calls == 3
    manager.close()
    store.close()


def test_sqlite_vec_index_round_trip(tmp_path: Path) -> None:
    index = SqliteVecIndex(tmp_path / "vectors.db", dimension=2)
    index.upsert(
        "generation",
        [
            VectorRecord("semantic:one", [1.0, 0.0], "local", "p1", "semantic"),
            VectorRecord("semantic:other", [0.0, 1.0], "other", "p1", "semantic"),
        ],
    )

    hits = index.search(
        [1.0, 0.0],
        filters=VectorFilters(generation_id="generation", owner_user_id="local"),
        limit=2,
    )

    assert [hit.evidence_id for hit in hits] == ["semantic:one"]
    index.delete("generation", ["semantic:one"])
    assert (
        index.search(
            [1.0, 0.0],
            filters=VectorFilters(generation_id="generation", owner_user_id="local"),
            limit=2,
        )
        == []
    )
    index.close()
