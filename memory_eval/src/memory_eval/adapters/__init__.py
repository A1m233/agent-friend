"""适配层：统一 :class:`EvalCase` ↔ ``memory`` 公共接口。"""

from __future__ import annotations

from .memory_adapter import BENCHMARK_PERSONA_ID, ingest_case, retrieve_for_question

__all__ = [
    "BENCHMARK_PERSONA_ID",
    "ingest_case",
    "retrieve_for_question",
]
