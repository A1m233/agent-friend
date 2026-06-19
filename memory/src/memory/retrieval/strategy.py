"""召回策略：把"按 query 找候选记忆"抽成可替换接口。

v1 提供 :class:`KeywordRetrieval`（SQLite FTS5 trigram）；future 的
``VectorRetrieval``（Chroma + BGE）只需实现同一 :class:`RetrievalStrategy`、
构造时替换注入，``Memory.retrieve`` 与调用方都不动（design §6.3）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from ..contracts import Layer

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..store import SqliteMemoryStore

__all__ = ["Candidate", "KeywordRetrieval", "RetrievalStrategy"]


@dataclass(frozen=True)
class Candidate:
    """一条召回候选（打分前的中间态）。

    Attributes:
        text: 正文（semantic.statement / episodic.summary）。
        layer: ``"semantic"`` 或 ``"episodic"``。
        source_ref: 条目标识（id），供 observability。
        importance: 条目重要性 [0,1]。
        ts: 用于时间衰减的参考时间（semantic=updated_at / episodic=occurred_at）。
        relevance_raw: 召回器给的原始相关度（bm25，越小越相关）；打分阶段归一化。
    """

    text: str
    layer: Layer
    source_ref: str
    importance: float
    ts: datetime
    relevance_raw: float


class RetrievalStrategy(Protocol):
    """召回接口。"""

    def search(
        self, query: str, *, owner_user_id: str, persona_id: str, limit: int
    ) -> list[Candidate]:
        """按 query 返回候选（未打分、未截断到最终 top-k）。"""
        ...


class KeywordRetrieval:
    """v1 关键词召回：合并 semantic + episodic 的 FTS 命中。

    Args:
        store: 记忆库。
        per_layer_limit: 每层最多取多少条候选喂给打分（打分后再统一 top-k）。
    """

    def __init__(self, store: SqliteMemoryStore, *, per_layer_limit: int = 20) -> None:
        self._store = store
        self._per_layer_limit = per_layer_limit

    def search(
        self, query: str, *, owner_user_id: str, persona_id: str, limit: int
    ) -> list[Candidate]:
        logger.info(
            "search query_len=%d owner=%s persona=%s limit=%d per_layer=%d",
            len(query),
            owner_user_id,
            persona_id,
            limit,
            self._per_layer_limit,
        )
        cands: list[Candidate] = []
        for row, bm25 in self._store.search_semantic(
            query, owner_user_id=owner_user_id, persona_id=persona_id, limit=self._per_layer_limit
        ):
            cands.append(
                Candidate(
                    text=row.statement,
                    layer="semantic",
                    source_ref=row.id,
                    importance=row.importance,
                    ts=row.updated_at,
                    relevance_raw=bm25,
                )
            )
        for ep, bm25 in self._store.search_episodic(
            query, owner_user_id=owner_user_id, persona_id=persona_id, limit=self._per_layer_limit
        ):
            cands.append(
                Candidate(
                    text=ep.summary,
                    layer="episodic",
                    source_ref=ep.id,
                    importance=ep.importance,
                    ts=ep.occurred_at,
                    relevance_raw=bm25,
                )
            )
        if cands:
            scores = sorted(c.relevance_raw for c in cands)[:5]
            logger.info("search returned %d candidates (top5 bm25=%s)", len(cands), scores)
        else:
            logger.info("search returned 0 candidates")
        return cands
