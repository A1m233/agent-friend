"""pass-1 M13.3：pinned 注入相关性 gate。

issue 003 主因 2 的修复：当 query 与 pinned 条目无关时，pinned 不应作为占位
安慰品被注入（pre-pass-1 行为是无条件全部注入）。

架构：复用 M13.2 落地的 jieba FTS5 索引（``store.fts_match_pinned``），
零额外依赖、零额外查询通道。详见
docs/requirements/013-memory-quality-pass-1/design.md §5。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contracts import GateMode
    from ..store import SemanticRow, SqliteMemoryStore

logger = logging.getLogger(__name__)

__all__ = ["pinned_gate"]

_SHORT_QUERY_THRESHOLD = 6
"""短 query 阈值（字符数）：低于此长度的 query 在 ``lenient`` 档下直接通过 gate，
不走 FTS5 命中判定。兜底"我叫什么"/"我是谁"这类极短身份提问（design §5.3）。"""


def pinned_gate(
    query: str,
    pinned: list[SemanticRow],
    *,
    store: SqliteMemoryStore,
    owner_user_id: str,
    mode: GateMode = "lenient",
) -> list[SemanticRow]:
    """按 query 相关性过滤 pinned，避免无关 query 让 pinned 作为占位安慰品注入。

    Args:
        query: retrieve 的 query。
        pinned: 已经从 store 拉出来的 pinned 列表（按 ``store.pinned`` 的排序）。
        store: 用于 FTS5 命中判定（复用 M13.2 落地的 jieba 索引）。
        owner_user_id: pinned 是 user 维度，按 owner 隔离。
        mode: 阈值档位，``strict`` / ``lenient``。

    Returns:
        过滤后的 pinned（保持原顺序）。空 query / 空 pinned 透传原 pinned，
        保持调用方对"无 query 时只查 pinned"行为的依赖。
    """
    if not pinned or not query.strip():
        logger.info(
            "pinned_gate pass-through pinned=%d query_empty=%s", len(pinned), not query.strip()
        )
        return pinned
    if mode == "lenient" and len(query.strip()) < _SHORT_QUERY_THRESHOLD:
        logger.info("pinned_gate lenient short-query pass-through pinned=%d", len(pinned))
        return pinned
    hit_ids = store.fts_match_pinned(query, owner_user_id=owner_user_id)
    filtered = [p for p in pinned if p.id in hit_ids]
    logger.info(
        "pinned_gate matched=%d of %d pinned (mode=%s hit_ids=%d)",
        len(filtered),
        len(pinned),
        mode,
        len(hit_ids),
    )
    if not hit_ids:
        return []
    return filtered
