"""评测数据层：基准原始格式 → 统一 :class:`EvalCase`。"""

from __future__ import annotations

from .case import EvalCase, EvalQuestion, EvalTurn
from .core_profile import load_core_profile_cases
from .locomo import load_locomo
from .perltqa import load_perltqa
from .semantic_changes import load_semantic_change_cases

__all__ = [
    "EvalCase",
    "EvalQuestion",
    "EvalTurn",
    "load_core_profile_cases",
    "load_locomo",
    "load_perltqa",
    "load_semantic_change_cases",
]
