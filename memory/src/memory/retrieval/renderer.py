"""把 pinned + 召回结果编排成一段可注入的 system 文本（design §6.1 / §6.4）。

对外是单一整体（:attr:`MemoryContext.rendered`），pinned 与召回的内部排布在此编排：
pinned 在前作"身份底色"，召回片段在后并带时间感（连续感，R-4.4）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from ..contracts import MemoryContext, MemoryItem
from ..search import EvidenceCandidate
from .strategy import Candidate

if TYPE_CHECKING:
    from ..store import SemanticRow

__all__ = ["Renderer"]

_PINNED_SCORE = 999.0
"""pinned 恒定置顶展示分（仅用于 items 排序展示，不参与召回打分）。"""


class Renderer:
    """召回结果 → :class:`MemoryContext`。"""

    def render(
        self,
        *,
        pinned: list[SemanticRow],
        recalled: list[tuple[Candidate, float]],
        now: datetime,
    ) -> MemoryContext:
        items: list[MemoryItem] = []
        lines: list[str] = []
        pinned_ids = {row.id for row in pinned}

        if pinned:
            lines.append("# 你对这位朋友一直记得的事")
            for row in pinned:
                lines.append(f"- {row.statement}")
                items.append(
                    MemoryItem(
                        text=row.statement,
                        layer="pinned",
                        source_ref=row.id,
                        score=_PINNED_SCORE,
                    )
                )

        recalled_lines: list[str] = []
        for cand, score in recalled:
            if cand.source_ref in pinned_ids:
                continue  # 已在 pinned 段出现，不重复
            suffix = _time_hint(cand, now)
            recalled_lines.append(f"- {cand.text}{suffix}")
            items.append(
                MemoryItem(
                    text=cand.text,
                    layer=cand.layer,
                    source_ref=cand.source_ref,
                    score=score,
                )
            )

        if recalled_lines:
            if lines:
                lines.append("")
            lines.append("# 这次让你想起的相关片段")
            lines.extend(recalled_lines)

        if not items:
            return MemoryContext.empty()
        return MemoryContext(rendered="\n".join(lines), items=items)

    def render_search(
        self,
        evidence: tuple[EvidenceCandidate, ...] | list[EvidenceCandidate],
        *,
        now: datetime,
    ) -> MemoryContext:
        """把统一搜索证据按 core / recalled / raw evidence 三段渲染。"""
        core = [candidate for candidate in evidence if candidate.kind == "core"]
        recalled = [
            candidate for candidate in evidence if candidate.kind in ("semantic", "episodic")
        ]
        raw = [candidate for candidate in evidence if candidate.kind == "utterance"]
        lines: list[str] = []
        items: list[MemoryItem] = []

        if core:
            lines.append("# 你一直记得的关系底色")
            for candidate in core:
                lines.append(f"- {candidate.text}")
                items.append(_search_item(candidate, score=_PINNED_SCORE))
        if recalled:
            if lines:
                lines.append("")
            lines.append("# 这次让你想起的相关记忆")
            for candidate in recalled:
                lines.append(f"- {candidate.text}{_evidence_time_hint(candidate, now)}")
                items.append(_search_item(candidate))
        if raw:
            if lines:
                lines.append("")
            lines.append("# 可核对的过往对话证据")
            for candidate in raw:
                lines.append(
                    f"- {_raw_evidence_text(candidate)}{_evidence_time_hint(candidate, now)}"
                )
                items.append(_search_item(candidate))
        if not items:
            return MemoryContext.empty()
        return MemoryContext(rendered="\n".join(lines), items=items)


def _search_item(candidate: EvidenceCandidate, *, score: float | None = None) -> MemoryItem:
    return MemoryItem(
        text=candidate.text,
        layer=candidate.kind,
        source_ref=candidate.evidence_id,
        score=candidate.fused_score or 0.0 if score is None else score,
    )


def _raw_evidence_text(candidate: EvidenceCandidate) -> str:
    if not candidate.excerpts:
        return candidate.text
    return " / ".join(
        f"{'用户' if excerpt.speaker == 'user' else '助手'}：{excerpt.text}"
        for excerpt in candidate.excerpts
    )


def _evidence_time_hint(candidate: EvidenceCandidate, now: datetime) -> str:
    occurred_at = candidate.occurred_at or candidate.valid_from
    if occurred_at is None:
        return ""
    local_date = _local_date(occurred_at)
    age_days = max(0, (_local_date(now) - local_date).days)
    if age_days == 0:
        relative = "今天"
    elif age_days == 1:
        relative = "昨天"
    else:
        relative = f"{age_days} 天前"
    return f"（{local_date.isoformat()}，{relative}）"


def _local_date(value: datetime) -> date:
    return value.astimezone().date()


def _time_hint(cand: Candidate, now: datetime) -> str:
    """给 episodic 片段加时间感后缀（连续感）；semantic 不加。"""
    if cand.layer != "episodic":
        return ""
    age_days = (_local_date(now) - _local_date(cand.ts)).days
    if age_days <= 0:
        return "（最近）"
    if age_days == 1:
        return "（昨天）"
    return f"（{age_days} 天前）"
