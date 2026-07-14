"""在调用方指定的 PYTHONPATH 下执行同一份 pass-1 parity fixture。"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from memory_eval.datasets import EvalCase, EvalQuestion, EvalTurn
from memory_eval.harness import runner
from memory_eval.harness.judge import AnchorRecallJudge


class SequenceLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses

    def complete(self, messages: list[dict[str, str]], **_kwargs: object) -> str:
        content = messages[-1]["content"]
        if "我叫小明" in content:
            return self._responses[0]
        if "我在腾讯工作" in content:
            return self._responses[1]
        if "我换到字节工作了" in content:
            return self._responses[2]
        if "我住在杭州" in content:
            return self._responses[3]
        raise AssertionError(f"未识别的 parity fragment: {content}")


def _fixture() -> tuple[EvalCase, list[str]]:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    turns = [
        EvalTurn("A", "我叫小明", now, "D1", 1),
        EvalTurn("B", "记住了", now, "D1", 1),
        EvalTurn("A", "我在腾讯工作", now + timedelta(days=1), "D2", 2),
        EvalTurn("A", "我换到字节工作了", now + timedelta(days=2), "D3", 3),
        EvalTurn("A", "我住在杭州，喜欢缅因猫", now + timedelta(days=3), "D4", 4),
    ]
    case = EvalCase(
        sample_id="parity-case",
        speaker_a="A",
        speaker_b="B",
        turns=turns,
        questions=[
            EvalQuestion("我的名字是什么？", "小明", "dialogues", [], ["小明"]),
            EvalQuestion("我现在在哪里工作？", "字节", "dialogues", [], ["字节"]),
            EvalQuestion("我喜欢什么猫？", "缅因猫", "dialogues", [], ["缅因猫"]),
        ],
    )
    responses = [
        '{"episodic_entries":[],"semantic_ops":['
        '{"op":"add","statement":"用户名字是小明","importance":0.9,'
        '"pinned":true,"speaker_origin":"user"}]}',
        '{"episodic_entries":[],"semantic_ops":['
        '{"op":"add","statement":"用户在腾讯工作","importance":0.6,'
        '"pinned":false,"speaker_origin":"user"}]}',
        '{"episodic_entries":[],"semantic_ops":['
        '{"op":"supersede","target_hint":"用户在腾讯工作",'
        '"statement":"用户在字节工作","importance":0.6,'
        '"pinned":false,"speaker_origin":"user"}]}',
        '{"episodic_entries":[],"semantic_ops":['
        '{"op":"add","statement":"用户住在杭州","importance":0.6,'
        '"pinned":false,"speaker_origin":"user"},'
        '{"op":"add","statement":"用户喜欢缅因猫","importance":0.5,'
        '"pinned":false,"speaker_origin":"user"}]}',
    ]
    return case, responses


def _database_rows(db_path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT statement, importance, pinned, valid_until, deleted_at "
            "FROM semantic ORDER BY statement"
        ).fetchall()
        return [
            {
                "statement": row["statement"],
                "importance": row["importance"],
                "pinned": row["pinned"],
                "is_historical": row["valid_until"] is not None,
                "is_deleted": row["deleted_at"] is not None,
            }
            for row in rows
        ]
    finally:
        connection.close()


def main() -> None:
    db_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    case, responses = _fixture()
    llm = cast(Any, SequenceLLM(responses))
    judge = AnchorRecallJudge()
    if hasattr(runner, "ingest_case_database"):
        from memory_eval.harness.factorial import build_factorial_corpora, factorial_db_path

        factorial_dir = db_path.parent / "factorial"
        build_factorial_corpora(
            [case],
            llm,
            variants={"E0": "pass1", "E1": "pass1"},
            output_dir=factorial_dir,
            run_metadata={"fixture": "origin-main-parity"},
        )
        db_path = factorial_db_path(factorial_dir, 0, "E0")
        outcome = runner.evaluate_case_database(
            case,
            db_path=db_path,
            judge=judge,
            retrieval_mode="legacy-keyword",
        )
    else:
        outcome = runner.run_case(case, llm, db_path=db_path, judge=judge)
    payload = {
        "database_rows": _database_rows(db_path),
        "questions": [
            {
                "question": item.question.question,
                "score": item.judge.score,
                "recalled": [
                    {
                        key: value
                        for key, value in asdict(memory).items()
                        if key not in ("score", "source_ref")
                    }
                    for memory in item.recalled
                ],
            }
            for item in outcome.outcomes
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
