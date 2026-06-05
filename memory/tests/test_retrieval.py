"""召回打分（scoring）与渲染（renderer）单测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memory.retrieval.scoring import _normalize_relevance, rank

from memory import Candidate, Renderer, SemanticRow


def _now() -> datetime:
    return datetime.now(UTC)


def _cand(
    text: str,
    *,
    layer: str = "semantic",
    importance: float = 0.5,
    age_days: float = 0.0,
    bm25: float = -1.0,
) -> Candidate:
    return Candidate(
        text=text,
        layer=layer,  # type: ignore[arg-type]
        source_ref=text,
        importance=importance,
        ts=_now() - timedelta(days=age_days),
        relevance_raw=bm25,
    )


def test_normalize_relevance_lower_bm25_is_better() -> None:
    # bm25 越小越相关 → 归一化后越接近 1
    rels = _normalize_relevance([-5.0, -1.0])
    assert rels[0] == 1.0
    assert rels[1] == 0.0


def test_normalize_relevance_all_equal() -> None:
    assert _normalize_relevance([-2.0, -2.0, -2.0]) == [1.0, 1.0, 1.0]


def test_rank_orders_by_combined_score_and_truncates() -> None:
    cands = [
        _cand("低分", importance=0.0, age_days=100, bm25=-1.0),
        _cand("高分", importance=1.0, age_days=0, bm25=-5.0),
    ]
    ranked = rank(cands, now=_now(), top_k=1)
    assert len(ranked) == 1
    assert ranked[0][0].text == "高分"


def test_rank_empty() -> None:
    assert rank([], now=_now(), top_k=5) == []


def _pinned_row(stmt: str, rid: str) -> SemanticRow:
    now = _now()
    return SemanticRow(
        id=rid, statement=stmt, persona_id="p1", created_at=now, updated_at=now, pinned=True
    )


def test_renderer_pinned_and_recalled() -> None:
    r = Renderer()
    pinned = [_pinned_row("用户叫小明", "name")]
    recalled = [(_cand("用户养了猫Tom", layer="semantic"), 1.2)]
    ctx = r.render(pinned=pinned, recalled=recalled, now=_now())

    assert not ctx.is_empty()
    assert "用户叫小明" in ctx.rendered
    assert "用户养了猫Tom" in ctx.rendered
    layers = {i.layer for i in ctx.items}
    assert layers == {"pinned", "semantic"}


def test_renderer_dedup_pinned_from_recalled() -> None:
    r = Renderer()
    pinned = [_pinned_row("用户叫小明", "name")]
    # 召回里混入同一条（source_ref == pinned id）应被去重
    dup = Candidate(
        text="用户叫小明",
        layer="semantic",
        source_ref="name",
        importance=0.9,
        ts=_now(),
        relevance_raw=-1.0,
    )
    ctx = r.render(pinned=pinned, recalled=[(dup, 2.0)], now=_now())
    assert ctx.rendered.count("用户叫小明") == 1
    assert len(ctx.items) == 1


def test_renderer_episodic_time_hint() -> None:
    r = Renderer()
    ep = _cand("聊到了旅行计划", layer="episodic", age_days=3)
    ctx = r.render(pinned=[], recalled=[(ep, 1.0)], now=_now())
    assert "3 天前" in ctx.rendered


def test_renderer_empty() -> None:
    ctx = Renderer().render(pinned=[], recalled=[], now=_now())
    assert ctx.is_empty()
    assert ctx.items == []
