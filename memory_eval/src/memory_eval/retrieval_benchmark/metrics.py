"""检索质量指标与 RRF 融合。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from .fixtures import BenchmarkQuery


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """用线性插值计算百分位；空输入返回 ``None``。"""
    if not values:
        return None
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile 必须位于 [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, limit: int, rank_constant: int = 60
) -> list[str]:
    """以稳定 tie-break 执行 Reciprocal Rank Fusion。"""
    scores: dict[str, float] = defaultdict(float)
    first_seen: dict[str, int] = {}
    seen_index = 0
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            if item_id not in first_seen:
                first_seen[item_id] = seen_index
                seen_index += 1
            scores[item_id] += 1.0 / (rank_constant + rank)
    ordered = sorted(scores, key=lambda item_id: (-scores[item_id], first_seen[item_id]))
    return ordered[:limit]


def evaluate_rankings(
    queries: Sequence[BenchmarkQuery], rankings: Mapping[str, Sequence[str]]
) -> dict[str, object]:
    """计算 Recall@1/3/5 与 MRR，并按 query category 分桶。"""
    buckets: dict[str, list[BenchmarkQuery]] = {"overall": list(queries)}
    for query in queries:
        buckets.setdefault(query.category, []).append(query)

    result: dict[str, object] = {}
    for category, items in buckets.items():
        reciprocal_ranks: list[float] = []
        recalls: dict[int, list[float]] = {1: [], 3: [], 5: []}
        for query in items:
            ranking = list(rankings.get(query.id, ()))
            expected = set(query.expected_ids)
            first_rank = next(
                (rank for rank, item_id in enumerate(ranking, start=1) if item_id in expected),
                None,
            )
            reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
            for cutoff in recalls:
                recalls[cutoff].append(1.0 if expected.intersection(ranking[:cutoff]) else 0.0)
        count = len(items)
        result[category] = {
            "query_count": count,
            "recall_at_1": _mean(recalls[1]),
            "recall_at_3": _mean(recalls[3]),
            "recall_at_5": _mean(recalls[5]),
            "mrr": _mean(reciprocal_ranks),
        }
    return result


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 6)
