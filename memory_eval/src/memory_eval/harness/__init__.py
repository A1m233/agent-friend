"""评测编排层：runner（跑流程）+ judge（判分扩展点）+ report（展示）。"""

from __future__ import annotations

from .factorial import build_factorial_corpora, factorial_db_path, summarize_factorial
from .judge import AnchorRecallJudge, Judge, JudgeResult, NoopJudge
from .paired_extraction import run_paired_extraction_probe
from .report import print_outcome, print_summary
from .runner import (
    CaseOutcome,
    MemoryConfig,
    QuestionOutcome,
    RetrievalMode,
    evaluate_case_database,
    ingest_case_database,
    run_case,
)
from .stability import run_stability_probe, select_stability_fragments
from .workspace import EvalWorkspace, WorkspaceManifest

__all__ = [
    "AnchorRecallJudge",
    "CaseOutcome",
    "EvalWorkspace",
    "Judge",
    "JudgeResult",
    "MemoryConfig",
    "NoopJudge",
    "QuestionOutcome",
    "RetrievalMode",
    "WorkspaceManifest",
    "build_factorial_corpora",
    "evaluate_case_database",
    "factorial_db_path",
    "ingest_case_database",
    "print_outcome",
    "print_summary",
    "run_case",
    "run_paired_extraction_probe",
    "run_stability_probe",
    "select_stability_fragments",
    "summarize_factorial",
]
