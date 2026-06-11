"""评测编排：单个 case 跑通 ingest → retrieve → judge，产出结构化结果。

每个 case 用**独立的库**（调用方传不同 ``db_path``）以隔离记忆，避免样本间串味。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from memory import build_memory
from memory_eval.adapters import ingest_case, retrieve_for_question

if TYPE_CHECKING:
    from pathlib import Path

    from llm_providers import LLMClient
    from memory import Memory, MemoryItem
    from memory_eval.datasets import EvalCase, EvalQuestion
    from memory_eval.harness.judge import Judge, JudgeResult

__all__ = ["CaseOutcome", "QuestionOutcome", "run_case"]


@dataclass(frozen=True)
class QuestionOutcome:
    """单题召回 + 判分结果。"""

    question: EvalQuestion
    recalled: list[MemoryItem]
    rendered: str
    judge: JudgeResult


@dataclass(frozen=True)
class CaseOutcome:
    """单个 case 的整体结果。"""

    sample_id: str
    n_turns: int
    outcomes: list[QuestionOutcome]


def run_case(
    case: EvalCase,
    llm_client: LLMClient,
    *,
    db_path: Path | str,
    judge: Judge,
    limit_questions: int | None = None,
) -> CaseOutcome:
    """灌入 ``case`` 的全部对话，再逐题召回 + 判分。

    Args:
        case: 评测样本。
        llm_client: 抽取用的 LLM 客户端（**真实调用**）。
        db_path: 本 case 专属库路径（隔离用）。
        judge: 判分器（PoC 用 ``NoopJudge``）。
        limit_questions: 只问前 N 题；``None`` 全问。

    Returns:
        本 case 的 :class:`CaseOutcome`。
    """
    memory = build_memory(db_path, llm_client)
    try:
        ingest_case(memory, case)
        questions = case.questions if limit_questions is None else case.questions[:limit_questions]
        outcomes = [_run_question(memory, question, judge) for question in questions]
    finally:
        memory.close()
    return CaseOutcome(sample_id=case.sample_id, n_turns=len(case.turns), outcomes=outcomes)


def _run_question(memory: Memory, question: EvalQuestion, judge: Judge) -> QuestionOutcome:
    context = retrieve_for_question(memory, question.question)
    return QuestionOutcome(
        question=question,
        recalled=context.items,
        rendered=context.rendered,
        judge=judge.judge(question, context),
    )
