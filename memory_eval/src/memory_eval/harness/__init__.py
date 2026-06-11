"""评测编排层：runner（跑流程）+ judge（判分扩展点）+ report（展示）。"""

from __future__ import annotations

from .judge import Judge, JudgeResult, NoopJudge
from .report import print_outcome
from .runner import CaseOutcome, QuestionOutcome, run_case

__all__ = [
    "CaseOutcome",
    "Judge",
    "JudgeResult",
    "NoopJudge",
    "QuestionOutcome",
    "print_outcome",
    "run_case",
]
