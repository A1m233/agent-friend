"""baseline.write 的 schema 锁定测试：构造 fake outcomes，验证落盘 JSON 字段齐备。

不依赖真实 LLM、不依赖真实 git 状态（git 字段降级为 nogit / None 都接受）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from memory_eval.datasets import EvalQuestion
from memory_eval.harness.baseline import BaselineRun, write
from memory_eval.harness.judge import JudgeResult
from memory_eval.harness.runner import CaseOutcome, QuestionOutcome

from memory import MemoryItem


def _outcome() -> CaseOutcome:
    q = EvalQuestion(
        question="Tom是什么？",
        answer="一只猫",
        category="dialogues",
        evidence=["1_0_0#0"],
        anchors=["猫", "Tom"],
    )
    item = MemoryItem(
        text="用户养了一只叫Tom的猫",
        layer="semantic",
        source_ref="case#1_0_0",
        score=0.9,
    )
    qo = QuestionOutcome(
        question=q,
        recalled=[item],
        rendered="用户养了一只叫Tom的猫",
        judge=JudgeResult(correct=True, detail="2/2 命中", score=1.0),
    )
    return CaseOutcome(sample_id="王小明", n_turns=10, outcomes=[qo])


def _run(tmp_path: Path) -> BaselineRun:
    return BaselineRun(
        started_at=datetime(2026, 6, 11, 7, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 6, 11, 7, 1, 30, tzinfo=UTC),
        dataset="perltqa",
        model="deepseek/deepseek-v4-flash",
        api_base=None,
        provider_defaults={"temperature": 0.7},
        limit_samples=1,
        limit_questions=1,
        note="schema lock test",
        dataset_paths=(tmp_path / "missing.json",),  # 缺失走防御路径
    )


def test_baseline_write_produces_v2_schema(tmp_path: Path) -> None:
    out_dir = tmp_path / "baselines"
    path = write([_outcome()], _run(tmp_path), out_dir)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))

    # schema 版本 + 文件名形态
    assert payload["schema_version"] == 2
    assert path.name.startswith("2026-06-11T07-00-00-")
    assert path.name.endswith(".json")


def test_baseline_run_section_complete(tmp_path: Path) -> None:
    out_dir = tmp_path / "baselines"
    path = write([_outcome()], _run(tmp_path), out_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    run = payload["run"]

    # 时间 / 时长
    assert run["started_at"].startswith("2026-06-11T07:00:00")
    assert run["ended_at"].startswith("2026-06-11T07:01:30")
    assert run["duration_seconds"] == 90.0

    # git 字段（取真实 git；非 git 环境是 nogit / None）
    assert isinstance(run["git_commit"], str)
    assert run["working_tree_dirty"] in (True, False, None)

    # 运行参数
    assert run["dataset"] == "perltqa"
    assert run["limit_samples"] == 1
    assert run["limit_questions"] == 1
    assert run["note"] == "schema lock test"

    # provider 段
    assert run["provider"]["model"] == "deepseek/deepseek-v4-flash"
    assert run["provider"]["api_base"] is None
    assert run["provider"]["defaults"] == {"temperature": 0.7}

    # dataset_files：缺失文件走防御路径
    files = run["dataset_files"]
    assert len(files) == 1
    assert files[0] == {"path": "missing.json", "sha256": None, "bytes": None}


def test_baseline_per_question_carries_recall_and_answer(tmp_path: Path) -> None:
    out_dir = tmp_path / "baselines"
    path = write([_outcome()], _run(tmp_path), out_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert len(payload["per_question"]) == 1
    q = payload["per_question"][0]

    # 稳定 ID + 题目元信息
    assert q["question_id"] == "王小明#q0"
    assert q["sample_id"] == "王小明"
    assert q["question"] == "Tom是什么？"
    assert q["answer"] == "一只猫"
    assert q["anchors"] == ["猫", "Tom"]

    # 判分结果
    assert q["score"] == 1.0
    assert "命中" in q["detail"]

    # 召回完整内容（v2 新增）：layer / text / source_ref / score 字段齐备
    assert q["recalled_count"] == 1
    assert len(q["recalled"]) == 1
    r = q["recalled"][0]
    assert r["text"] == "用户养了一只叫Tom的猫"
    assert r["layer"] == "semantic"
    assert r["source_ref"] == "case#1_0_0"
    assert r["score"] == 0.9


def test_baseline_macro_aggregates_only_scored(tmp_path: Path) -> None:
    """macro 跳过 score=None 的题（如 LoCoMo 走 NoopJudge）。"""
    q_scored = EvalQuestion(
        question="q1", answer="a", category="dialogues", evidence=[], anchors=["x"]
    )
    q_unscored = EvalQuestion(
        question="q2", answer="a", category="dialogues", evidence=[], anchors=[]
    )
    qo_scored = QuestionOutcome(
        question=q_scored,
        recalled=[],
        rendered="x",
        judge=JudgeResult(correct=True, detail="1/1", score=1.0),
    )
    qo_unscored = QuestionOutcome(
        question=q_unscored,
        recalled=[],
        rendered="",
        judge=JudgeResult(correct=None, detail="(无 anchor)", score=None),
    )
    outcome = CaseOutcome(sample_id="X", n_turns=1, outcomes=[qo_scored, qo_unscored])
    path = write([outcome], _run(tmp_path), tmp_path / "baselines")
    macro = json.loads(path.read_text(encoding="utf-8"))["macro"]
    assert macro["n_questions_total"] == 2
    assert macro["n_questions_scored"] == 1
    assert macro["score_mean"] == 1.0
    assert macro["n_questions_zero"] == 0
