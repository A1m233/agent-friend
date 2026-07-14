"""适配层：统一 :class:`EvalCase` ↔ ``memory`` 公共接口。"""

from __future__ import annotations

from .memory_adapter import BENCHMARK_PERSONA_ID, case_fragments, ingest_case, retrieve_for_question
from .session_history_adapter import case_history_source

__all__ = [
    "BENCHMARK_PERSONA_ID",
    "case_fragments",
    "case_history_source",
    "ingest_case",
    "retrieve_for_question",
]
