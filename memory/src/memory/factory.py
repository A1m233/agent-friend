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
from .store import SqliteMemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_providers import LLMClient

    from .contracts import RecallTrace
    from .extraction import ExtractionResult

__all__ = ["build_memory"]

_LEGACY_EXTRACT_PROMPT = Path(__file__).parent / "extraction" / "prompts" / "extract_legacy.md"
"""pre-pass-1 旧抽取 prompt 路径，供切片 baseline ``extraction_keep_specifics=False``。"""


def build_memory(
    db_path: Path | str,
    llm_client: LLMClient,
    *,
    on_extracted: Callable[[ExtractionResult], None] | None = None,
    on_retrieved: Callable[[RecallTrace], None] | None = None,
    extractor_prompt: str | None = None,
    weights: ScoreWeights | None = None,
    owner_user_id: str = "local",
    extraction_keep_specifics: bool = True,
    pinned_relevance_gate: bool = True,
) -> Memory:
    """装配一个用 SQLite + 关键词召回 + LLM 抽取的 :class:`Memory`。

    Args:
        db_path: 记忆库路径（父目录会自动建）。
        llm_client: 抽取用的 LLM 客户端（v1 与主对话同 provider 即可）。
        on_extracted: 抽取落库回调（observability / CLI 调试展示）。
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
    store = SqliteMemoryStore(db_path)

    prompt = extractor_prompt
    if prompt is None and not extraction_keep_specifics:
        prompt = _LEGACY_EXTRACT_PROMPT.read_text(encoding="utf-8")
    extractor = Extractor(llm_client, prompt=prompt)

    reconciler = Reconciler(store)
    retrieval = KeywordRetrieval(store)
    return Memory(
        store,
        extractor,
        reconciler,
        retrieval=retrieval,
        weights=weights,
        on_extracted=on_extracted,
        on_retrieved=on_retrieved,
        owner_user_id=owner_user_id,
        pinned_relevance_gate=pinned_relevance_gate,
    )
