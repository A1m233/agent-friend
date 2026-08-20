from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from memory_eval.datasets import EvalCase, EvalQuestion, EvalTurn
from memory_eval.harness.factorial import (
    build_factorial_corpora,
    factorial_db_path,
    summarize_factorial,
)
from memory_eval.harness.judge import JudgeResult
from memory_eval.harness.runner import CaseOutcome, QuestionOutcome

from llm_providers import ModelRequest, ModelResponse
from memory import SqliteMemoryStore


class OrderedLLM:
    def __init__(self) -> None:
        self.order: list[str] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        prompt = str(request.messages[0]["content"])
        self.order.append(prompt)
        fact = "基线事实" if prompt == "E0-prompt" else "候选事实"
        return ModelResponse(
            text=f'{{"episodic_entries":[],"semantic_ops":[{{"op":"add","statement":"{fact}"}}]}}'
        )


def _case() -> EvalCase:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    return EvalCase(
        sample_id="case-a",
        speaker_a="A",
        speaker_b="B",
        turns=[
            EvalTurn("A", "第一段", now, "D1", 1),
            EvalTurn("B", "第二段", now, "D2", 2),
        ],
        questions=[EvalQuestion("问题", "答案", "dialogues", [], ["答案"])],
    )


def test_factorial_corpora_ab_ba_checkpoint_rebuild_and_resume(tmp_path: Path) -> None:
    llm = OrderedLLM()
    root = tmp_path / "factorial"
    summary = build_factorial_corpora(
        [_case()],
        llm,  # type: ignore[arg-type]
        variants={"E0": "E0-prompt", "E1": "E1-prompt"},
        output_dir=root,
        run_metadata={"model": "fake", "dataset_files": [{"sha256": "abc"}]},
    )

    assert llm.order == ["E0-prompt", "E1-prompt", "E1-prompt", "E0-prompt"]
    assert summary == {
        "cases": 1,
        "variants": ["E0", "E1"],
        "expected_calls": 4,
        "completed_calls": 4,
    }
    for variant, statement in (("E0", "基线事实"), ("E1", "候选事实")):
        store = SqliteMemoryStore(factorial_db_path(root, 0, variant))
        try:
            assert {row.statement for row in store.list_semantic(owner_user_id="local")} == {
                statement
            }
        finally:
            store.close()

    resumed = build_factorial_corpora(
        [_case()],
        llm,  # type: ignore[arg-type]
        variants={"E0": "E0-prompt", "E1": "E1-prompt"},
        output_dir=root,
        run_metadata={"model": "fake", "dataset_files": [{"sha256": "abc"}]},
        resume=True,
    )
    assert resumed == summary
    assert len(llm.order) == 4

    with pytest.raises(RuntimeError, match="配置与 checkpoint 不一致"):
        build_factorial_corpora(
            [_case()],
            llm,  # type: ignore[arg-type]
            variants={"E0": "changed", "E1": "E1-prompt"},
            output_dir=root,
            run_metadata={"model": "fake", "dataset_files": [{"sha256": "abc"}]},
            resume=True,
        )


def _outcome(scores: list[float]) -> list[CaseOutcome]:
    question = EvalQuestion("问题", "答案", "dialogues", [], ["答案"])
    return [
        CaseOutcome(
            sample_id="case-a",
            n_turns=1,
            outcomes=[
                QuestionOutcome(
                    question=question,
                    recalled=[],
                    rendered="",
                    judge=JudgeResult(score=score, correct=score == 1, detail=""),
                )
                for score in scores
            ],
        )
    ]


def test_factorial_summary_enforces_strict_gates_and_paired_ci() -> None:
    report = summarize_factorial(
        {
            "E0R0": _outcome([0.0] * 20),
            "E0R1": _outcome([0.2] * 20),
            "E1R0": _outcome([0.1] * 20),
            "E1R1": _outcome([0.5] * 20),
        },
        bootstrap_samples=1000,
    )

    assert report["all_gates_passed"] is True
    assert report["full_vs_baseline"]["paired_bootstrap_95_ci"] == [0.5, 0.5]  # type: ignore[index]

    failed = summarize_factorial(
        {
            "E0R0": _outcome([0.4, 0.4]),
            "E0R1": _outcome([0.4, 0.4]),
            "E1R0": _outcome([0.3, 0.3]),
            "E1R1": _outcome([0.39, 0.39]),
        },
        bootstrap_samples=100,
    )
    assert failed["all_gates_passed"] is False
    assert failed["gates"]["extraction_non_regression"] is False  # type: ignore[index]
