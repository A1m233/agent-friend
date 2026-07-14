"""评测编排：把一次真实抽取与多种确定性检索模式拆开。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from memory import (
    Extractor,
    KeywordRetrieval,
    Memory,
    MemorySearchService,
    OnnxEmbeddingProvider,
    Reconciler,
    SqliteMemoryStore,
    SqliteVecIndex,
    build_memory,
)
from memory_eval.adapters import case_history_source, ingest_case, retrieve_for_question

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_providers import LLMClient
    from memory import EmbeddingProvider, ExtractionAttempt, MemoryItem, RecallTrace, VectorIndex
    from memory_eval.datasets import EvalCase, EvalQuestion
    from memory_eval.harness.judge import Judge, JudgeResult

__all__ = [
    "CaseOutcome",
    "MemoryConfig",
    "QuestionOutcome",
    "RetrievalMode",
    "evaluate_case_database",
    "ingest_case_database",
    "run_case",
]

RetrievalMode = Literal[
    "legacy-keyword",
    "keyword",
    "hybrid",
    "unified-keyword",
    "unified-hybrid",
]


@dataclass(frozen=True)
class MemoryConfig:
    """抽取侧 ablation 开关；检索模式由 evaluator 单独控制。"""

    extraction_keep_specifics: bool = True
    pinned_relevance_gate: bool = True


@dataclass(frozen=True)
class QuestionOutcome:
    """单题召回、判分与可选检索 trace。"""

    question: EvalQuestion
    recalled: list[MemoryItem]
    rendered: str
    judge: JudgeResult
    trace: dict[str, object] | None = None


@dataclass(frozen=True)
class CaseOutcome:
    """单个 case 的结果；包含本次实际检索模式与 dense 状态。"""

    sample_id: str
    n_turns: int
    outcomes: list[QuestionOutcome]
    retrieval_mode: RetrievalMode = "keyword"
    index_state: dict[str, object] | None = None


class _NoCallLLM:
    """重放检索时的防误调用桩；任何抽取调用都说明 evaluator 越界。"""

    def complete(self, *_args: object, **_kwargs: object) -> str:
        raise RuntimeError("检索重放不得调用 LLM")


def ingest_case_database(
    case: EvalCase,
    llm_client: LLMClient,
    *,
    db_path: Path | str,
    memory_config: MemoryConfig | None = None,
    on_extraction_attempt: Callable[[ExtractionAttempt], None] | None = None,
) -> None:
    """只做真实抽取并持久化 DB，不执行任何评测查询。"""
    cfg = memory_config or MemoryConfig()
    memory = build_memory(
        db_path,
        llm_client,
        auto_dense=False,
        on_extraction_attempt=on_extraction_attempt,
        **asdict(cfg),
    )
    try:
        ingest_case(memory, case)
    finally:
        memory.close()


def evaluate_case_database(
    case: EvalCase,
    *,
    db_path: Path | str,
    judge: Judge,
    retrieval_mode: RetrievalMode,
    limit_questions: int | None = None,
    pinned_relevance_gate: bool = True,
    dense_model_dir: Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_index: VectorIndex | None = None,
) -> CaseOutcome:
    """在已有 DB 上评测；不会观察 fragment，也不会调用 LLM。"""
    traces: list[RecallTrace] = []
    base_mode = _base_retrieval_mode(retrieval_mode)
    search_service = (
        MemorySearchService([case_history_source(case)])
        if retrieval_mode.startswith("unified-")
        else None
    )
    memory = _build_replay_memory(
        Path(db_path),
        retrieval_mode=base_mode,
        pinned_relevance_gate=pinned_relevance_gate,
        dense_model_dir=dense_model_dir,
        embedding_provider=embedding_provider,
        vector_index=vector_index,
        traces=traces,
        search_service=search_service,
    )
    try:
        index_state = _prepare_index(memory, base_mode)
        index_state["session_history_enabled"] = search_service is not None
        questions = case.questions if limit_questions is None else case.questions[:limit_questions]
        outcomes = [_run_question(memory, question, judge, traces) for question in questions]
    finally:
        memory.close()
    return CaseOutcome(
        sample_id=case.sample_id,
        n_turns=len(case.turns),
        outcomes=outcomes,
        retrieval_mode=retrieval_mode,
        index_state=index_state,
    )


def run_case(
    case: EvalCase,
    llm_client: LLMClient,
    *,
    db_path: Path | str,
    judge: Judge,
    limit_questions: int | None = None,
    memory_config: MemoryConfig | None = None,
) -> CaseOutcome:
    """兼容旧调用：先抽取落库，再用当前 keyword 路径重放。"""
    cfg = memory_config or MemoryConfig()
    ingest_case_database(case, llm_client, db_path=db_path, memory_config=cfg)
    return evaluate_case_database(
        case,
        db_path=db_path,
        judge=judge,
        retrieval_mode="keyword",
        limit_questions=limit_questions,
        pinned_relevance_gate=cfg.pinned_relevance_gate,
    )


def _build_replay_memory(
    db_path: Path,
    *,
    retrieval_mode: RetrievalMode,
    pinned_relevance_gate: bool,
    dense_model_dir: Path | None,
    embedding_provider: EmbeddingProvider | None,
    vector_index: VectorIndex | None,
    traces: list[RecallTrace],
    search_service: MemorySearchService | None,
) -> Memory:
    no_call_llm = cast("LLMClient", _NoCallLLM())
    if retrieval_mode == "legacy-keyword":
        if search_service is not None:
            raise ValueError("legacy-keyword 不支持统一 history source")
        store = SqliteMemoryStore(db_path)
        return Memory(
            store,
            Extractor(no_call_llm),
            Reconciler(store),
            retrieval=KeywordRetrieval(store),
            pinned_relevance_gate=pinned_relevance_gate,
            on_retrieved=traces.append,
        )

    if retrieval_mode == "keyword":
        return build_memory(
            db_path,
            no_call_llm,
            pinned_relevance_gate=pinned_relevance_gate,
            auto_dense=False,
            on_retrieved=traces.append,
            search_service=search_service,
        )

    if (embedding_provider is None) != (vector_index is None):
        raise ValueError("hybrid 重放必须同时提供 embedding_provider 与 vector_index")
    if embedding_provider is None:
        if dense_model_dir is None:
            raise RuntimeError("hybrid 重放缺少 dense_model_dir")
        embedding_provider, vector_index = _dense_components(db_path, dense_model_dir)
    return build_memory(
        db_path,
        no_call_llm,
        pinned_relevance_gate=pinned_relevance_gate,
        embedding_provider=embedding_provider,
        vector_index=vector_index,
        auto_dense=False,
        on_retrieved=traces.append,
        search_service=search_service,
    )


def _base_retrieval_mode(
    retrieval_mode: RetrievalMode,
) -> Literal["legacy-keyword", "keyword", "hybrid"]:
    if retrieval_mode == "unified-keyword":
        return "keyword"
    if retrieval_mode == "unified-hybrid":
        return "hybrid"
    return retrieval_mode


def _dense_components(
    db_path: Path,
    model_dir: Path,
) -> tuple[EmbeddingProvider, VectorIndex]:
    model_path = model_dir / "onnx" / "model_quantized.onnx"
    tokenizer_path = model_dir / "tokenizer.json"
    manifest_path = model_dir / "manifest.json"
    missing = [path for path in (model_path, tokenizer_path, manifest_path) if not path.is_file()]
    if missing:
        names = ", ".join(path.name for path in missing)
        raise RuntimeError(f"hybrid 所需 dense 资产不完整: {names}; model_dir={model_dir}")
    provider = OnnxEmbeddingProvider(model_path, tokenizer_path)
    index = SqliteVecIndex(db_path.with_name(f"{db_path.stem}-vectors-512.db"), dimension=512)
    return provider, index


def _prepare_index(memory: Memory, retrieval_mode: RetrievalMode) -> dict[str, object]:
    if retrieval_mode != "hybrid":
        return {
            "keyword_ready": True,
            "dense_status": "disabled",
            "active_generation": None,
        }
    state = memory.rebuild_dense_index()
    if state is None:
        raise RuntimeError("hybrid 模式未装配 dense index")
    if (
        state.status != "ready"
        or state.active_generation != state.generation_id
        or state.indexed_count != state.total_count
    ):
        raise RuntimeError(
            "hybrid dense index 未 ready: "
            f"status={state.status} active={state.active_generation} "
            f"generation={state.generation_id} indexed={state.indexed_count}/{state.total_count} "
            f"error={state.last_error}"
        )
    return {
        "keyword_ready": True,
        "dense_status": state.status,
        "generation_id": state.generation_id,
        "active_generation": state.active_generation,
        "indexed_count": state.indexed_count,
        "total_count": state.total_count,
        "last_error": state.last_error,
    }


def _run_question(
    memory: Memory,
    question: EvalQuestion,
    judge: Judge,
    traces: list[RecallTrace],
) -> QuestionOutcome:
    before = len(traces)
    context = retrieve_for_question(memory, question.question)
    trace = traces[-1] if len(traces) > before else None
    return QuestionOutcome(
        question=question,
        recalled=context.items,
        rendered=context.rendered,
        judge=judge.judge(question, context),
        trace=_trace_payload(trace),
    )


def _trace_payload(trace: RecallTrace | None) -> dict[str, object] | None:
    if trace is None:
        return None
    return {
        "intent": trace.intent,
        "time_range": trace.time_range,
        "mode": trace.mode,
        "source_runs": trace.source_runs,
        "candidates": trace.candidates,
        "budget": trace.budget,
        "index_state": trace.index_state,
    }
