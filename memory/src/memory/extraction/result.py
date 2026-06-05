"""抽取管线的数据结构：LLM 输出（解析后）与落库结果（可观测）。

详见 docs/requirements/008-engine-memory/design.md §5.2 / §8。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = ["ExtractionOutput", "ExtractionResult", "SemanticOp"]

OpKind = Literal["add", "supersede"]


@dataclass(frozen=True)
class SemanticOp:
    """LLM 提议的一次语义记忆操作。

    Attributes:
        op: ``add`` 新增 / ``supersede`` 取代某条旧事实。
        statement: 第三人称、原子化的事实陈述。
        importance: LLM 给的重要性初值 [0,1]。
        pinned: 是否"每次必进上下文"（仅姓名 / 核心关系等稳定身份事实置真）。
        speaker_origin: 该事实主要基于谁的话（``user`` / ``agent``）；
            ``user`` 在落库时加一档重要性。
        target_hint: ``supersede`` 时指向被取代旧事实的近似陈述，供 reconcile 定位。
    """

    op: OpKind
    statement: str
    importance: float = 0.5
    pinned: bool = False
    speaker_origin: str = "user"
    target_hint: str | None = None


@dataclass(frozen=True)
class ExtractionOutput:
    """对一个 fragment 抽取后、解析好的 LLM 产物。"""

    episodic_summary: str | None
    semantic_ops: list[SemanticOp] = field(default_factory=list)

    def is_noop(self) -> bool:
        """既没有可记的情节摘要、也没有语义操作。"""
        return not self.episodic_summary and not self.semantic_ops


@dataclass(frozen=True)
class ExtractionResult:
    """一次抽取真正落库的结果，供 observability（design §8）。"""

    session_id: str
    episodic_id: str | None = None
    added_semantic: list[str] = field(default_factory=list)  # "statement" 文本
    superseded_semantic: list[str] = field(default_factory=list)  # 旧 statement 文本

    def is_empty(self) -> bool:
        return self.episodic_id is None and not self.added_semantic and not self.superseded_semantic
