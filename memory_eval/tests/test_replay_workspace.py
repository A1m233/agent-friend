from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from memory.search import VectorFilters, VectorHit, VectorRecord
from memory_eval.datasets import EvalCase, EvalQuestion, EvalTurn
from memory_eval.harness import AnchorRecallJudge
from memory_eval.harness.runner import evaluate_case_database, ingest_case_database
from memory_eval.harness.workspace import EvalWorkspace, WorkspaceManifest

from llm_providers import ModelRequest, ModelResponse
from memory import ExtractionAttempt


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=(
                '{"episodic_entries": [], "semantic_ops": '
                '[{"op": "add", "statement": "用户养了一只叫Tom的猫"}]}'
            )
        )


class EmptyLLM:
    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text='{"episodic_entries": [], "semantic_ops": []}')


class FakeProvider:
    model_id = "fake-replay-v1"
    dimension = 2

    def embed(self, texts: list[str]) -> list[list[float]]:
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
            for (generation, evidence_id), record in self.records.items()
            if generation == filters.generation_id and record.owner_user_id == filters.owner_user_id
        ]
        return [VectorHit(evidence_id=item, distance=0.1) for item in ids[:limit]]

    def delete(self, generation_id: str, evidence_ids: list[str]) -> None:
        for evidence_id in evidence_ids:
            self.records.pop((generation_id, evidence_id), None)

    def close(self) -> None:
        pass


def _case() -> EvalCase:
    now = datetime.now(UTC)
    return EvalCase(
        sample_id="case-a",
        speaker_a="A",
        speaker_b="B",
        turns=[
            EvalTurn("A", "我养了一只猫叫Tom", now, "D1:1", 1),
            EvalTurn("B", "真可爱", now, "D1:2", 1),
        ],
        questions=[EvalQuestion("Tom是什么？", "一只猫", "dialogues", ["D1:1"], ["Tom", "猫"])],
    )


def _manifest(tmp_path: Path) -> WorkspaceManifest:
    dataset = tmp_path / "dataset.json"
    dataset.write_text("{}", encoding="utf-8")
    return WorkspaceManifest.build(
        dataset="perltqa",
        sample_ids=["case-a"],
        model="fake-model",
        api_base=None,
        provider_defaults={},
        extraction_keep_specifics=True,
        pinned_relevance_gate=True,
        dataset_paths=(dataset,),
    )


def test_one_ingest_can_replay_all_retrieval_modes_without_more_llm(tmp_path: Path) -> None:
    case = _case()
    llm = FakeLLM()
    db_path = tmp_path / "memory.db"
    ingest_case_database(case, llm, db_path=db_path)  # type: ignore[arg-type]

    keyword = evaluate_case_database(
        case,
        db_path=db_path,
        judge=AnchorRecallJudge(),
        retrieval_mode="keyword",
    )
    legacy = evaluate_case_database(
        case,
        db_path=db_path,
        judge=AnchorRecallJudge(),
        retrieval_mode="legacy-keyword",
    )
    hybrid = evaluate_case_database(
        case,
        db_path=db_path,
        judge=AnchorRecallJudge(),
        retrieval_mode="hybrid",
        embedding_provider=FakeProvider(),
        vector_index=FakeIndex(),
    )

    assert llm.calls == 1
    assert keyword.outcomes[0].judge.score == 1.0
    assert legacy.outcomes[0].judge.score == 1.0
    assert keyword.outcomes[0].trace is not None
    assert keyword.outcomes[0].trace["candidates"]
    assert hybrid.index_state is not None
    assert hybrid.index_state["dense_status"] == "ready"
    assert hybrid.index_state["indexed_count"] == hybrid.index_state["total_count"]
    trace = hybrid.outcomes[0].trace
    assert trace is not None
    candidates = cast(list[dict[str, object]], trace["candidates"])
    assert any(item["dense_rank"] is not None for item in candidates)


def test_unified_replay_uses_production_history_fallback_without_llm(tmp_path: Path) -> None:
    case = replace(
        _case(),
        questions=[
            EvalQuestion(
                "我们之前聊过 猫",
                "一只猫",
                "dialogues",
                ["D1:1"],
                ["Tom", "猫"],
            )
        ],
    )
    db_path = tmp_path / "empty-memory.db"
    ingest_case_database(case, EmptyLLM(), db_path=db_path)  # type: ignore[arg-type]

    structured_only = evaluate_case_database(
        case,
        db_path=db_path,
        judge=AnchorRecallJudge(),
        retrieval_mode="keyword",
    )
    unified = evaluate_case_database(
        case,
        db_path=db_path,
        judge=AnchorRecallJudge(),
        retrieval_mode="unified-keyword",
    )

    assert structured_only.outcomes[0].judge.score == 0.0
    assert unified.outcomes[0].judge.score == 1.0
    assert unified.index_state is not None
    assert unified.index_state["session_history_enabled"] is True
    assert unified.outcomes[0].trace is not None
    source_runs = cast(list[dict[str, object]], unified.outcomes[0].trace["source_runs"])
    assert any(
        run["source"] == "session_history" and cast(int, run["candidate_count"]) > 0
        for run in source_runs
    )


def test_hybrid_without_assets_fails_closed(tmp_path: Path) -> None:
    case = _case()
    llm = FakeLLM()
    db_path = tmp_path / "memory.db"
    ingest_case_database(case, llm, db_path=db_path)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="dense 资产不完整"):
        evaluate_case_database(
            case,
            db_path=db_path,
            judge=AnchorRecallJudge(),
            retrieval_mode="hybrid",
            dense_model_dir=tmp_path / "missing-model",
        )


@pytest.mark.skipif(
    not os.environ.get("AGENT_FRIEND_TEST_DENSE_MODEL_DIR"),
    reason="真实 ONNX 资产 smoke 仅在显式给出本地模型目录时运行",
)
def test_real_onnx_hybrid_replay_reaches_ready(tmp_path: Path) -> None:
    model_dir = Path(os.environ["AGENT_FRIEND_TEST_DENSE_MODEL_DIR"])
    case = _case()
    db_path = tmp_path / "memory.db"
    ingest_case_database(case, FakeLLM(), db_path=db_path)  # type: ignore[arg-type]

    outcome = evaluate_case_database(
        case,
        db_path=db_path,
        judge=AnchorRecallJudge(),
        retrieval_mode="hybrid",
        dense_model_dir=model_dir,
    )

    assert outcome.index_state is not None
    assert outcome.index_state["dense_status"] == "ready"
    assert outcome.index_state["indexed_count"] == outcome.index_state["total_count"]
    trace = outcome.outcomes[0].trace
    assert trace is not None
    candidates = cast(list[dict[str, object]], trace["candidates"])
    assert any(item["dense_rank"] is not None for item in candidates)


def test_workspace_checkpoints_round_trip_and_rejects_mismatch(tmp_path: Path) -> None:
    workspace = EvalWorkspace(tmp_path / "run")
    manifest = _manifest(tmp_path)
    workspace.create(manifest)
    case = _case()
    ingest_case_database(case, FakeLLM(), db_path=workspace.case_db(0))  # type: ignore[arg-type]
    workspace.mark_ingested(0, "case-a")
    outcome = evaluate_case_database(
        case,
        db_path=workspace.case_db(0),
        judge=AnchorRecallJudge(),
        retrieval_mode="keyword",
    )
    workspace.save_outcome(0, outcome, result_key="keyword-q1")

    assert workspace.is_ingested(0, "case-a")
    assert workspace.assert_compatible(manifest) == manifest
    assert workspace.load_outcome(0, result_key="keyword-q1") == outcome

    mismatch = replace(manifest, model="other-model")
    with pytest.raises(RuntimeError, match="拒绝混用抽取产物"):
        workspace.assert_compatible(mismatch)


def test_workspace_checkpoints_extraction_attempts_and_summary(tmp_path: Path) -> None:
    workspace = EvalWorkspace(tmp_path / "run")
    workspace.create(_manifest(tmp_path))
    attempt = ExtractionAttempt(
        session_id="case-a",
        first_source_ref="case-a#D1",
        last_source_ref="case-a#D1",
        utterance_count=2,
        existing_fact_count=0,
        status="success",
        duration_ms=12.5,
        episodic_count=1,
        semantic_count=2,
        raw_response='{"episodic_entries":["原始响应"]}',
    )

    workspace.append_extraction_attempt(0, attempt)
    summary = workspace.write_extraction_summary(0)

    assert workspace.load_extraction_attempts(0) == [attempt]
    assert summary["attempt_count"] == 1
    assert summary["status_counts"] == {"success": 1}
    assert summary["episodic_count"] == 1
    assert summary["semantic_count"] == 2
