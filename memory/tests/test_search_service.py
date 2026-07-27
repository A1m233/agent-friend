"""M48.1 统一 search service 与结构化 source 测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from memory.search import (
    AnalyzedQuery,
    EvidenceCandidate,
    MemorySearchService,
    SearchRequest,
    StructuredMemorySource,
)
from memory.store import EpisodicRow, SemanticRow, SqliteMemoryStore

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _candidate(evidence_id: str, source_name: str, rank: int) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=evidence_id,
        source_name=source_name,
        kind="semantic",
        text=evidence_id,
        owner_user_id="local",
        persona_id="p1",
        occurred_at=_NOW,
        valid_from=None,
        valid_until=None,
        importance=0.5,
        provenance=(),
        keyword_rank=rank,
    )


class _Source:
    def __init__(
        self,
        name: str,
        candidates: list[EvidenceCandidate],
        *,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self._candidates = candidates
        self._error = error

    def search(
        self,
        query: AnalyzedQuery,
        *,
        request: SearchRequest,
        limit: int,
    ) -> list[EvidenceCandidate]:
        del query, request
        if self._error is not None:
            raise self._error
        return self._candidates[:limit]


def _request(**kwargs: object) -> SearchRequest:
    values: dict[str, object] = {
        "text": "日语",
        "owner_user_id": "local",
        "persona_id": "p1",
        "mode": "explicit",
        "top_k": 2,
    }
    values.update(kwargs)
    return SearchRequest(**values)  # type: ignore[arg-type]


def test_service_scopes_sources_dedupes_and_applies_limit() -> None:
    first = _Source("first", [_candidate("same", "first", 2), _candidate("a", "first", 1)])
    second = _Source(
        "second",
        [replace(_candidate("same", "second", 1), text="duplicate"), _candidate("b", "second", 2)],
    )
    service = MemorySearchService([first, second])

    result = service.search(_request())
    scoped = service.search(_request(source_scope=frozenset({"second"})))

    assert [item.evidence_id for item in result.evidence] == ["a", "same"]
    assert [item.evidence_id for item in scoped.evidence] == ["same", "b"]


def test_service_keeps_partial_results_when_one_source_fails() -> None:
    service = MemorySearchService(
        [
            _Source("broken", [], error=RuntimeError("disk unavailable")),
            _Source("healthy", [_candidate("ok", "healthy", 1)]),
        ]
    )

    result = service.search(_request())

    assert [item.evidence_id for item in result.evidence] == ["ok"]
    assert result.failures[0].source_name == "broken"


def test_service_can_rank_legacy_candidate_without_any_timestamp() -> None:
    legacy = replace(
        _candidate("legacy", "source", 1),
        occurred_at=None,
        valid_from=None,
    )
    service = MemorySearchService([_Source("source", [legacy])])

    result = service.search(_request())

    assert [item.evidence_id for item in result.evidence] == ["legacy"]


def test_duplicate_source_registration_is_rejected() -> None:
    service = MemorySearchService([_Source("same", [])])

    try:
        service.register_source(_Source("same", []))
    except ValueError as exc:
        assert "重复注册" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate source should fail")


def test_structured_source_projects_semantic_and_persona_scoped_episodic() -> None:
    store = SqliteMemoryStore(":memory:")
    try:
        store.add_semantic(
            SemanticRow(
                id="s1",
                statement="用户正在学习日语",
                persona_id="other-persona",
                created_at=_NOW,
                updated_at=_NOW,
                valid_from=_NOW,
            )
        )
        store.add_episodic(
            EpisodicRow(
                id="e1",
                summary="我们讨论了日语学习方法",
                source_ref="session#one",
                persona_id="p1",
                occurred_at=_NOW,
                created_at=_NOW,
            )
        )
        store.add_episodic(
            EpisodicRow(
                id="e2",
                summary="另一个人格聊了日语",
                source_ref="session#two",
                persona_id="p2",
                occurred_at=_NOW,
                created_at=_NOW,
            )
        )
        service = MemorySearchService([StructuredMemorySource(store)])

        result = service.search(_request(top_k=10))

        assert {item.evidence_id for item in result.evidence} == {"semantic:s1", "episodic:e1"}
        assert all(item.owner_user_id == "local" for item in result.evidence)
    finally:
        store.close()


def test_automatic_history_source_runs_when_structured_evidence_is_insufficient() -> None:
    structured = _Source("structured_memory", [])
    history = _Source("session_history", [_candidate("history", "session_history", 1)])
    service = MemorySearchService([history, structured])

    general = service.search(_request(text="日语", mode="automatic", top_k=8))
    historical = service.search(_request(text="我们上次聊了什么", mode="automatic", top_k=8))

    assert [item.evidence_id for item in general.evidence] == ["history"]
    assert general.source_runs[-1].source_name == "session_history"
    assert general.source_runs[-1].status == "ok"
    assert [item.evidence_id for item in historical.evidence] == ["history"]
    assert historical.source_runs[-1].status == "ok"


def test_automatic_history_source_skips_when_structured_query_coverage_is_sufficient() -> None:
    structured = _Source(
        "structured_memory", [_candidate("用户正在学习日语", "structured_memory", 1)]
    )
    history = _Source("session_history", [_candidate("history", "session_history", 1)])
    service = MemorySearchService([history, structured])

    result = service.search(_request(text="日语", mode="automatic", top_k=8))

    assert [item.evidence_id for item in result.evidence] == ["用户正在学习日语"]
    assert result.source_runs[-1].source_name == "session_history"
    assert result.source_runs[-1].status == "skipped"


def test_automatic_history_fallback_uses_separate_raw_evidence_budget() -> None:
    structured = _Source(
        "structured_memory",
        [_candidate("只覆盖别的内容", "structured_memory", 1)],
    )
    history = _Source(
        "session_history",
        [replace(_candidate("日语原话", "session_history", 1), kind="utterance")],
    )
    service = MemorySearchService([history, structured])

    result = service.search(_request(text="日语", mode="automatic", top_k=1))

    assert [item.evidence_id for item in result.evidence] == ["只覆盖别的内容", "日语原话"]
    assert {
        decision.reason for decision in result.decisions if decision.decision == "injected"
    } == {"ordinary_budget", "raw_evidence_budget"}


def test_rrf_combines_keyword_and_dense_rank() -> None:
    hybrid = replace(_candidate("hybrid", "structured_memory", 8), dense_rank=1)
    keyword_only = _candidate("keyword", "structured_memory", 1)
    service = MemorySearchService([_Source("structured_memory", [keyword_only, hybrid])])

    result = service.search(_request(top_k=2))

    assert [item.evidence_id for item in result.evidence] == ["hybrid", "keyword"]
    assert result.evidence[0].fused_score is not None


def test_core_has_independent_count_and_character_budget() -> None:
    core = [
        replace(
            _candidate(f"core-{index}", "structured_memory", index + 1),
            kind="core",
            text=f"{index}-" + "身份" * 20,
        )
        for index in range(10)
    ]
    ordinary = [_candidate("ordinary", "structured_memory", 1)]
    service = MemorySearchService([_Source("structured_memory", [*core, *ordinary])])

    result = service.search(_request(top_k=1))

    assert sum(item.kind == "core" for item in result.evidence) == 6
    assert result.evidence[-1].evidence_id == "ordinary"


def test_explicit_search_does_not_fill_empty_result_with_unmatched_core() -> None:
    core = replace(
        _candidate("core", "structured_memory", 1),
        kind="core",
        text="用户喜欢蓝色",
        keyword_rank=None,
        dense_rank=None,
    )
    service = MemorySearchService([_Source("structured_memory", [core])])

    result = service.search(_request(text="完全无关的话题", top_k=5))

    assert result.evidence == ()
    assert any(decision.reason == "explicit_unmatched_core" for decision in result.decisions)


def test_structured_source_distinguishes_current_and_historical_fact_versions() -> None:
    store = SqliteMemoryStore(":memory:")
    old_time = datetime(2025, 1, 1, tzinfo=UTC)
    new_time = datetime(2026, 1, 1, tzinfo=UTC)
    old = SemanticRow(
        id="old-job",
        statement="用户以前在腾讯工作",
        persona_id="p1",
        created_at=old_time,
        updated_at=old_time,
        valid_from=old_time,
        fact_key="user.employer",
    )
    new = SemanticRow(
        id="new-job",
        statement="用户现在在字节工作",
        persona_id="p1",
        created_at=new_time,
        updated_at=new_time,
        valid_from=new_time,
        fact_key="user.employer",
        supersedes_id="old-job",
    )
    store.add_semantic(old)
    store.supersede_and_add_semantic("old-job", new, new_time)
    service = MemorySearchService([StructuredMemorySource(store)])

    current = service.search(_request(text="用户现在在哪里工作", top_k=5))
    historical = service.search(_request(text="用户以前在哪里工作", top_k=5))

    assert [item.evidence_id for item in current.evidence] == ["semantic:new-job"]
    assert [item.evidence_id for item in historical.evidence] == ["semantic:old-job"]
    store.close()
