"""召回打分：``relevance × importance × time-decay`` 的简化三因子（design §6.2）。

参考 Generative Agents 的 relevance / importance / recency 三因子，但简化为线性
加权。权重与衰减常量集中在此，调参不散落。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .strategy import Candidate

__all__ = ["ScoreWeights", "rank"]


@dataclass(frozen=True)
class ScoreWeights:
    """打分权重与时间衰减。

    Attributes:
        w_relevance / w_importance / w_recency: 三因子线性权重。
        decay_per_day: 时间衰减底数，``recency = decay_per_day ** age_days``。
    """

    w_relevance: float = 1.0
    w_importance: float = 0.6
    w_recency: float = 0.4
    decay_per_day: float = 0.98


DEFAULT_WEIGHTS = ScoreWeights()


def rank(
    candidates: list[Candidate],
    *,
    now: datetime,
    top_k: int,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> list[tuple[Candidate, float]]:
    """对候选打分排序，返回前 ``top_k`` 的 ``(candidate, score)``（按分数倒序）。"""
    if not candidates:
        return []

    relevances = _normalize_relevance([c.relevance_raw for c in candidates])
    scored: list[tuple[Candidate, float]] = []
    for cand, rel in zip(candidates, relevances, strict=True):
        recency = _recency(cand.ts, now, weights.decay_per_day)
        score = (
            weights.w_relevance * rel
            + weights.w_importance * cand.importance
            + weights.w_recency * recency
        )
        scored.append((cand, score))

    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]


def _normalize_relevance(raws: list[float]) -> list[float]:
    """bm25（越小越相关）→ [0,1]（越大越相关）。

    全部相等（含单条）时统一给 1.0。
    """
    lo, hi = min(raws), max(raws)
    if math.isclose(lo, hi):
        return [1.0 for _ in raws]
    span = hi - lo
    return [(hi - x) / span for x in raws]


def _recency(ts: datetime, now: datetime, decay_per_day: float) -> float:
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return float(decay_per_day**age_days)
