"""评测数据层：基准原始格式 → 统一 :class:`EvalCase`。"""

from __future__ import annotations

from .case import EvalCase, EvalQuestion, EvalTurn
from .locomo import load_locomo
from .perltqa import load_perltqa

__all__ = [
    "EvalCase",
    "EvalQuestion",
    "EvalTurn",
    "load_locomo",
    "load_perltqa",
]
