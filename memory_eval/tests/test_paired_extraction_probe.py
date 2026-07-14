from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from memory_eval.datasets import EvalCase, EvalQuestion, EvalTurn
from memory_eval.harness.paired_extraction import run_paired_extraction_probe


class PromptAwareLLM:
    def __init__(self) -> None:
        self.order: list[str] = []

    def complete(self, messages: list[dict[str, str]], **_kwargs: object) -> str:
        variant = messages[0]["content"]
        self.order.append(variant)
        if variant == "pass1-prompt":
            return (
                '{"episodic_entries":[],"semantic_ops":['
                '{"op":"add","statement":"用户喜欢缅因猫MaineCoon"}]}'
            )
        return '{"episodic_entries":[],"semantic_ops":[]}'


def _case() -> EvalCase:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    return EvalCase(
        sample_id="case-a",
        speaker_a="A",
        speaker_b="B",
        turns=[
            EvalTurn("A", "我喜欢缅因猫 MaineCoon", now, "D1", 1),
            EvalTurn("B", "记住了", now, "D1", 1),
            EvalTurn("A", "我的编号是A1024", now, "D2", 2),
        ],
        questions=[EvalQuestion("喜欢什么？", "缅因猫", "dialogues", [])],
    )


def test_paired_probe_ab_ba_checkpoint_summary_and_resume(tmp_path: Path) -> None:
    llm = PromptAwareLLM()
    output_dir = tmp_path / "paired"
    summary = run_paired_extraction_probe(
        [_case()],
        llm,  # type: ignore[arg-type]
        variants={"pass1": "pass1-prompt", "current": "current-prompt"},
        output_dir=output_dir,
        fragment_count=2,
        repeats=2,
        run_metadata={
            "model": "fake",
            "temperature": 0.7,
            "code": "abc",
            "dataset_files": [{"path": "fake.json", "sha256": "123"}],
        },
    )

    assert llm.order == [
        "pass1-prompt",
        "current-prompt",
        "current-prompt",
        "pass1-prompt",
        "current-prompt",
        "pass1-prompt",
        "pass1-prompt",
        "current-prompt",
    ]
    assert summary["completed_calls"] == 8
    assert summary["variants"]["pass1"]["status_counts"] == {"success": 4}  # type: ignore[index]
    assert summary["variants"]["current"]["status_counts"] == {  # type: ignore[index]
        "empty_output": 4
    }
    assert summary["paired"]["baseline_success_candidate_not"] == 4  # type: ignore[index]
    assert summary["paired"]["regression_count"] >= 4  # type: ignore[index]

    records = [
        json.loads(line)
        for line in (output_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 8
    assert {record["order_position"] for record in records} == {1, 2}
    assert all(record["attempt"]["raw_response"] for record in records)

    resumed = run_paired_extraction_probe(
        [_case()],
        llm,  # type: ignore[arg-type]
        variants={"pass1": "pass1-prompt", "current": "current-prompt"},
        output_dir=output_dir,
        fragment_count=2,
        repeats=2,
        run_metadata={
            "model": "fake",
            "temperature": 0.7,
            "code": "abc",
            "dataset_files": [{"path": "fake.json", "sha256": "123"}],
        },
        resume=True,
    )
    assert resumed == summary
    assert len(llm.order) == 8


def test_paired_probe_fails_closed_when_prompt_or_run_metadata_changes(tmp_path: Path) -> None:
    llm = PromptAwareLLM()
    output_dir = tmp_path / "paired"
    common = {
        "cases": [_case()],
        "llm_client": llm,
        "output_dir": output_dir,
        "fragment_count": 1,
        "repeats": 1,
    }
    run_paired_extraction_probe(
        **common,  # type: ignore[arg-type]
        variants={"pass1": "pass1-prompt", "current": "current-prompt"},
        run_metadata={"model": "fake"},
    )

    with pytest.raises(RuntimeError, match="配置与 checkpoint 不一致"):
        run_paired_extraction_probe(
            **common,  # type: ignore[arg-type]
            variants={"pass1": "changed", "current": "current-prompt"},
            run_metadata={"model": "fake"},
            resume=True,
        )

    with pytest.raises(RuntimeError, match="配置与 checkpoint 不一致"):
        run_paired_extraction_probe(
            **common,  # type: ignore[arg-type]
            variants={"pass1": "pass1-prompt", "current": "current-prompt"},
            run_metadata={"model": "different"},
            resume=True,
        )
