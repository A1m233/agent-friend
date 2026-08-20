from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from memory_eval.datasets import EvalCase, EvalQuestion, EvalTurn
from memory_eval.harness.stability import run_stability_probe, select_stability_fragments

from llm_providers import ModelRequest, ModelResponse


class AlternatingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        statement = "用户喜欢猫" if self.calls % 2 else "用户喜欢小猫"
        return ModelResponse(
            text=f'{{"episodic_entries":[],"semantic_ops":[{{"op":"add","statement":"{statement}"}}]}}'
        )


def _case() -> EvalCase:
    now = datetime.now(UTC)
    return EvalCase(
        sample_id="case-a",
        speaker_a="A",
        speaker_b="B",
        turns=[
            EvalTurn("A", "我喜欢猫", now, "D1", 1),
            EvalTurn("B", "记住了", now, "D1", 1),
            EvalTurn("A", "我也喜欢狗", now, "D2", 2),
        ],
        questions=[EvalQuestion("喜欢什么？", "猫", "dialogues", [])],
    )


def test_stability_probe_is_deterministic_checkpointed_and_resumable(tmp_path: Path) -> None:
    case = _case()
    selected_once = select_stability_fragments([case], 2)
    selected_twice = select_stability_fragments([case], 2)
    assert [item[1].utterances[0].source_ref for item in selected_once] == [
        item[1].utterances[0].source_ref for item in selected_twice
    ]

    llm = AlternatingLLM()
    summary = run_stability_probe(
        [case],
        llm,  # type: ignore[arg-type]
        output_dir=tmp_path / "stability",
        fragment_count=2,
        repeats=2,
    )
    assert llm.calls == 4
    assert summary["completed_calls"] == 4
    assert summary["unstable_output_probes"] == 2
    assert summary["stable_output_probes"] == 0
    assert summary["always_success_probes"] == 2
    assert summary["always_empty_probes"] == 0
    assert summary["mixed_status_probes"] == 0
    assert summary["per_repeat"] == [
        {
            "repeat": 1,
            "completed_calls": 2,
            "status_counts": {"success": 2},
            "semantic_count": 2,
            "episodic_count": 0,
        },
        {
            "repeat": 2,
            "completed_calls": 2,
            "status_counts": {"success": 2},
            "semantic_count": 2,
            "episodic_count": 0,
        },
    ]

    resumed = run_stability_probe(
        [case],
        llm,  # type: ignore[arg-type]
        output_dir=tmp_path / "stability",
        fragment_count=2,
        repeats=2,
        resume=True,
    )
    assert llm.calls == 4
    assert resumed == summary
    records = [
        json.loads(line)
        for line in (tmp_path / "stability" / "attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all(record["attempt"]["raw_response"] for record in records)

    changed_case = replace(case, sample_id="case-b")
    try:
        run_stability_probe(
            [changed_case],
            llm,  # type: ignore[arg-type]
            output_dir=tmp_path / "stability",
            fragment_count=2,
            repeats=2,
            resume=True,
        )
    except RuntimeError as exc:
        assert "配置与 checkpoint 不一致" in str(exc)
    else:
        raise AssertionError("配置变化时必须拒绝混用 checkpoint")


def test_stability_probe_ids_distinguish_same_source_ref_with_different_content(
    tmp_path: Path,
) -> None:
    case = _case()
    duplicate_refs = EvalCase(
        sample_id=case.sample_id,
        speaker_a=case.speaker_a,
        speaker_b=case.speaker_b,
        turns=[
            replace(turn, dia_id="same-source", session_index=index)
            for index, turn in enumerate(case.turns, start=1)
        ],
        questions=case.questions,
    )

    selected = select_stability_fragments([duplicate_refs], 3)
    llm = AlternatingLLM()
    summary = run_stability_probe(
        [duplicate_refs],
        llm,  # type: ignore[arg-type]
        output_dir=tmp_path / "collision-proof",
        fragment_count=3,
        repeats=1,
    )

    assert len(selected) == 3
    assert len({selected_item[1].utterances[0].text for selected_item in selected}) == 3
    assert llm.calls == 3
    assert summary["completed_probes"] == 3
