"""M48.0 benchmark 的确定性机制测试；不下载模型、不调用 LLM。"""

from __future__ import annotations

from pathlib import Path

import pytest
from memory_eval.retrieval_benchmark.fixtures import benchmark_documents, benchmark_queries
from memory_eval.retrieval_benchmark.metrics import (
    evaluate_rankings,
    percentile,
    reciprocal_rank_fusion,
)
from memory_eval.retrieval_benchmark.runner import (
    _open_vec_db,
    _sqlite_vec_search,
    run_history_benchmark,
    run_hybrid_benchmark,
    run_keyword_benchmark,
)


def test_benchmark_fixture_scales_without_changing_targets() -> None:
    documents = benchmark_documents(100)
    assert len(documents) == 100
    assert documents[0].id == "pet-name"
    assert documents[-1].id.startswith("noise-")
    assert len({document.id for document in documents}) == 100


def test_percentile_and_rrf_are_deterministic() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([], 0.95) is None
    assert reciprocal_rank_fusion([["a", "b", "c"], ["b", "d", "a"]], limit=4) == [
        "b",
        "a",
        "d",
        "c",
    ]


def test_evaluate_rankings_splits_keyword_and_paraphrase() -> None:
    queries = benchmark_queries()[:2]
    rankings = {queries[0].id: ["pet-name"], queries[1].id: ["noise-00000", "pet-name"]}
    result = evaluate_rankings(queries, rankings)
    overall = result["overall"]
    assert isinstance(overall, dict)
    assert overall["recall_at_1"] == 0.5
    assert overall["recall_at_3"] == 1.0
    assert "keyword" in result
    assert "paraphrase" in result


def test_keyword_and_hybrid_benchmark_use_production_fts(tmp_path: Path) -> None:
    documents = benchmark_documents(40)
    queries = benchmark_queries()
    keyword, rankings = run_keyword_benchmark(
        documents,
        queries,
        db_path=tmp_path / "memory.db",
        top_k=10,
        iterations=1,
    )
    assert keyword["status"] == "ok"
    quality = keyword["quality"]
    assert isinstance(quality, dict)
    assert isinstance(quality["overall"], dict)
    assert quality["overall"]["recall_at_1"] > 0

    hybrid, fused = run_hybrid_benchmark(
        queries,
        rankings,
        rankings,
        top_k=10,
        iterations=1,
    )
    assert hybrid["status"] == "ok"
    assert fused == rankings


def test_history_benchmark_records_no_content(tmp_path: Path) -> None:
    result = run_history_benchmark(
        source="synthetic",
        synthetic_dir=tmp_path / "sessions",
        session_count=3,
        messages_per_session=4,
        iterations=1,
    )
    assert result["status"] == "ok"
    assert result["session_count"] == 3
    assert result["content_recorded"] is False
    assert result["result_counts"] == [0]


def test_sqlite_vec_keeps_stable_external_ids(tmp_path: Path) -> None:
    sqlite_vec = pytest.importorskip("sqlite_vec")
    db = _open_vec_db(tmp_path / "vectors.db", sqlite_vec=sqlite_vec, dimension=2)
    db.executemany(
        "INSERT INTO vector_ids(id, external_id) VALUES (?, ?)",
        [(1, "fact-a"), (2, "fact-b")],
    )
    db.executemany(
        "INSERT INTO vector_index(id, embedding) VALUES (?, ?)",
        [
            (1, sqlite_vec.serialize_float32([1.0, 0.0])),
            (2, sqlite_vec.serialize_float32([0.0, 1.0])),
        ],
    )
    db.commit()
    assert _sqlite_vec_search(
        db,
        sqlite_vec=sqlite_vec,
        query_vector=_ListVector([1.0, 0.0]),
        top_k=2,
    ) == ["fact-a", "fact-b"]
    db.close()


class _ListVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)
