"""把 pinned + 召回结果编排成一段可注入的 system 文本（design §6.1 / §6.4）。

对外是单一整体（:attr:`MemoryContext.rendered`），pinned 与召回的内部排布在此编排：
pinned 在前作"身份底色"，召回片段在后并带时间感（连续感，R-4.4）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..contracts import MemoryContext, MemoryItem
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


def _time_hint(cand: Candidate, now: datetime) -> str:
    """给 episodic 片段加时间感后缀（连续感）；semantic 不加。"""
    if cand.layer != "episodic":
        return ""
    age_days = (now - cand.ts).days
    if age_days <= 0:
        return "（最近）"
    if age_days == 1:
        return "（昨天）"
    return f"（{age_days} 天前）"
