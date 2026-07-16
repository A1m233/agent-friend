"""``build_memory``：把记忆系统的各组件装配成可用的 :class:`Memory` 门面。

调用方（``tools.cli`` / 未来 HTTP backend / 桌宠前端）只需提供"库路径 + 一个
LLMClient"，不必关心 store / extractor / reconciler / retrieval / renderer 怎么拼。
换召回策略（向量）、换打分权重等都在此集中调整，调用方不动（design §3 / §10）。

Pass-1（013 M13.4）：加两个 ablation 切片开关供 evaluator 跑切片 baseline。
详见 docs/requirements/013-memory-quality-pass-1/design.md §6。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .extraction import Extractor, Reconciler
from .facade import Memory
from .retrieval import KeywordRetrieval, ScoreWeights
from .search import (
    EmbeddingIndexManager,
    EmbeddingProvider,
    MemorySearchService,
    OnnxEmbeddingProvider,
    SqliteVecIndex,
    StructuredMemorySource,
    VectorIndex,
)
from .store import SqliteMemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_providers import LLMClient

    from .contracts import RecallTrace
    from .extraction import ExtractionAttempt, ExtractionResult

__all__ = ["build_memory"]

_LEGACY_EXTRACT_PROMPT = Path(__file__).parent / "extraction" / "prompts" / "extract_legacy.md"
"""pre-pass-1 旧抽取 prompt 路径，供切片 baseline ``extraction_keep_specifics=False``。"""

_DENSE_MODEL_DIR = Path("models") / "bge-small-zh-v1.5"
_DENSE_MODEL_FILE = Path("onnx") / "model_quantized.onnx"


def build_memory(
    db_path: Path | str,
    llm_client: LLMClient,
    *,
    on_extracted: Callable[[ExtractionResult], None] | None = None,
    on_extraction_attempt: Callable[[ExtractionAttempt], None] | None = None,
    on_retrieved: Callable[[RecallTrace], None] | None = None,
    extractor_prompt: str | None = None,
    weights: ScoreWeights | None = None,
    owner_user_id: str = "local",
    extraction_keep_specifics: bool = True,
    pinned_relevance_gate: bool = True,
    search_service: MemorySearchService | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_index: VectorIndex | None = None,
    auto_dense: bool = True,
    dense_model_dir: Path | str | None = None,
) -> Memory:
    """装配一个用 SQLite + 关键词召回 + LLM 抽取的 :class:`Memory`。

    Args:
        db_path: 记忆库路径（父目录会自动建）。
        llm_client: 抽取用的 LLM 客户端（v1 与主对话同 provider 即可）。
        on_extracted: 抽取落库回调（observability / CLI 调试展示）。
        on_extraction_attempt: 每次 LLM 抽取尝试的诊断回调；默认关闭，不持久化原始响应。
        on_retrieved: 召回完成回调（observability / inspector）。
        extractor_prompt: 覆盖默认抽取 prompt（测试 / 调参）。若同时给了
            ``extractor_prompt`` 和 ``extraction_keep_specifics=False``，
            ``extractor_prompt`` 优先（显式参数覆盖切片开关）。
        weights: 召回打分权重；默认 :class:`ScoreWeights`。
        owner_user_id: 多 user 预留维度，v1 固定。
        extraction_keep_specifics: pass-1 切片开关——``True``（默认）用新 pass-1
            prompt（保留具体词）；``False`` 用 ``extract_legacy.md`` 旧 prompt
            （pre-pass-1 行为）。供 evaluator 切片 baseline 对照 M13.1 改进。
        pinned_relevance_gate: pass-1 切片开关——``True``（默认）按 query 相关性
            过滤 pinned 注入；``False`` pinned 全量注入（pre-pass-1 行为）。
            供 evaluator 切片 baseline 对照 M13.3 改进。

    Returns:
        装配好的 :class:`Memory`，可直接 ``observe`` / ``retrieve``。
    """
    if search_service is not None and StructuredMemorySource.name in search_service.source_names:
        raise ValueError("search_service 已注册 structured_memory")
    if (embedding_provider is None) != (vector_index is None):
        raise ValueError("embedding_provider 与 vector_index 必须同时提供")
    db_path = Path(db_path)
    if embedding_provider is None and auto_dense:
        discovered = _discover_dense_components(
            db_path,
            dense_model_dir=Path(dense_model_dir) if dense_model_dir is not None else None,
        )
        if discovered is not None:
            embedding_provider, vector_index = discovered
    store = SqliteMemoryStore(db_path)
    index_manager = (
        EmbeddingIndexManager(
            store,
            embedding_provider,
            vector_index,
            owner_user_id=owner_user_id,
        )
        if embedding_provider is not None and vector_index is not None
        else None
    )
    service = search_service or MemorySearchService()
    service.register_source(StructuredMemorySource(store, index_manager=index_manager))

    prompt = extractor_prompt
    if prompt is None and not extraction_keep_specifics:
        prompt = _LEGACY_EXTRACT_PROMPT.read_text(encoding="utf-8")
    extractor = Extractor(llm_client, prompt=prompt, on_attempt=on_extraction_attempt)

    reconciler = Reconciler(store)
    retrieval = KeywordRetrieval(store)
    memory = Memory(
        store,
        extractor,
        reconciler,
        retrieval=retrieval,
        weights=weights,
        on_extracted=on_extracted,
        on_retrieved=on_retrieved,
        owner_user_id=owner_user_id,
        pinned_relevance_gate=pinned_relevance_gate,
        search_service=service,
        index_manager=index_manager,
    )
    return memory


def _discover_dense_components(
    db_path: Path,
    *,
    dense_model_dir: Path | None = None,
) -> tuple[EmbeddingProvider, VectorIndex] | None:
    """发现由 benchmark 安装入口准备的生产资产；不在启动时联网。"""
    model_dir = dense_model_dir or db_path.parent / _DENSE_MODEL_DIR
    model_path = model_dir / _DENSE_MODEL_FILE
    tokenizer_path = model_dir / "tokenizer.json"
    manifest_path = model_dir / "manifest.json"
    if not model_path.exists() or not tokenizer_path.exists() or not manifest_path.exists():
        return None
    provider = OnnxEmbeddingProvider(model_path, tokenizer_path)
    index = SqliteVecIndex(
        db_path.with_name(f"{db_path.stem}-vectors-512.db"),
        dimension=512,
    )
    return provider, index
