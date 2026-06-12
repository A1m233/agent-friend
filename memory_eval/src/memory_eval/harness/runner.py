"""评测编排：单个 case 跑通 ingest → retrieve → judge，产出结构化结果。

每个 case 用**独立的库**（调用方传不同 ``db_path``）以隔离记忆，避免样本间串味。

Pass-1（013 M13.4）：加 :class:`MemoryConfig` 透传 ablation 切片开关给 ``build_memory``，
让评测能跑 ``pass-1-full`` / ``pass-1-only-extraction`` / ``pass-1-only-pinned`` /
``pass-1-baseline`` 4 份切片对照。详见 docs/requirements/013-memory-quality-pass-1/design.md §6。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from memory import build_memory
from memory_eval.adapters import ingest_case, retrieve_for_question

if TYPE_CHECKING:
    from pathlib import Path

    from llm_providers import LLMClient
    from memory import Memory, MemoryItem
    from memory_eval.datasets import EvalCase, EvalQuestion
    from memory_eval.harness.judge import Judge, JudgeResult

__all__ = ["CaseOutcome", "MemoryConfig", "QuestionOutcome", "run_case"]


@dataclass(frozen=True)
class MemoryConfig:
    """ablation 切片开关（pass-1 M13.4，design §6）。默认全开 = pass-1 终态。

    跑切片 baseline 时关单个开关跑 ``pass-1-only-extraction`` /
    ``pass-1-only-pinned`` / ``pass-1-baseline``，跟 ``pass-1-full`` 和
    ``2026-06-12T01-31-46-46e810d.json``（pre-pass-1）对照。
    """

    extraction_keep_specifics: bool = True
    pinned_relevance_gate: bool = True


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
    memory_config: MemoryConfig | None = None,
) -> CaseOutcome:
    """灌入 ``case`` 的全部对话，再逐题召回 + 判分。

    Args:
        case: 评测样本。
        llm_client: 抽取用的 LLM 客户端（**真实调用**）。
        db_path: 本 case 专属库路径（隔离用）。
        judge: 判分器（PoC 用 ``NoopJudge``）。
        limit_questions: 只问前 N 题；``None`` 全问。
        memory_config: pass-1 ablation 切片开关，``None`` 等价于默认全开。

    Returns:
        本 case 的 :class:`CaseOutcome`。
    """
    cfg = memory_config or MemoryConfig()
    memory = build_memory(db_path, llm_client, **asdict(cfg))
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
