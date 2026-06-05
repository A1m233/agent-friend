"""``build_memory``：把记忆系统的各组件装配成可用的 :class:`Memory` 门面。

调用方（``tools.cli`` / 未来 HTTP backend / 桌宠前端）只需提供"库路径 + 一个
LLMClient"，不必关心 store / extractor / reconciler / retrieval / renderer 怎么拼。
换召回策略（向量）、换打分权重等都在此集中调整，调用方不动（design §3 / §10）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .extraction import Extractor, Reconciler
from .facade import Memory
from .retrieval import KeywordRetrieval, ScoreWeights
from .store import SqliteMemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from llm_providers import LLMClient

    from .extraction import ExtractionResult

__all__ = ["build_memory"]


def build_memory(
    db_path: Path | str,
    llm_client: LLMClient,
    *,
    on_extracted: Callable[[ExtractionResult], None] | None = None,
    extractor_prompt: str | None = None,
    weights: ScoreWeights | None = None,
    owner_user_id: str = "local",
) -> Memory:
    """装配一个用 SQLite + 关键词召回 + LLM 抽取的 :class:`Memory`。

    Args:
        db_path: 记忆库路径（父目录会自动建）。
        llm_client: 抽取用的 LLM 客户端（v1 与主对话同 provider 即可）。
        on_extracted: 抽取落库回调（observability / CLI 调试展示）。
        extractor_prompt: 覆盖默认抽取 prompt（测试 / 调参）。
        weights: 召回打分权重；默认 :class:`ScoreWeights`。
        owner_user_id: 多 user 预留维度，v1 固定。

    Returns:
        装配好的 :class:`Memory`，可直接 ``observe`` / ``retrieve``。
    """
    store = SqliteMemoryStore(db_path)
    extractor = Extractor(llm_client, prompt=extractor_prompt)
    reconciler = Reconciler(store)
    retrieval = KeywordRetrieval(store)
    return Memory(
        store,
        extractor,
        reconciler,
        retrieval=retrieval,
        weights=weights,
        on_extracted=on_extracted,
        owner_user_id=owner_user_id,
    )
