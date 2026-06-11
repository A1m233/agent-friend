"""判分扩展点：协议 + NoopJudge 桩 + AnchorRecallJudge（PerLTQA 锚点召回）。

判分接口的设计意图见 011 design §6.2：传入 ``(question, context)``，返回 :class:`JudgeResult`；
新增 judge 只需新增一个实现，runner / report 不动。

本模块当前两种实现：

- :class:`NoopJudge`：不判分，对应无 ground-truth 锚点的数据（LoCoMo 等）。
- :class:`AnchorRecallJudge`：基于 PerLTQA ``Memory Anchors`` 做 substring 召回判分，
  输出 ``score ∈ [0, 1]``。已知偏差与升级路径见 012 design §8。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from memory import MemoryContext
    from memory_eval.datasets import EvalQuestion

__all__ = ["AnchorRecallJudge", "Judge", "JudgeResult", "NoopJudge"]


@dataclass(frozen=True)
class JudgeResult:
    """单题判分结果。

    Attributes:
        correct: 是否答对的二值视图；``None`` 表示未判分。对 AnchorRecallJudge 而言，
            ``correct=(score == 1.0)``——anchor 全命中视作"答对"（仅作展示，分析与对比
            应优先看 ``score``）。
        detail: 说明文本（judge 的理由 / 占位说明 / 命中比例 + 未命中清单等）。
        score: 连续判分结果 ``∈ [0, 1]``；``None`` 表示未判分。Macro 平均 / baseline
            对比的基础字段。
    """

    correct: bool | None
    detail: str
    score: float | None = None


class Judge(Protocol):
    """判分协议：给定问题 + 召回上下文，产出 :class:`JudgeResult`。"""

    def judge(self, question: EvalQuestion, context: MemoryContext) -> JudgeResult: ...


class NoopJudge:
    """不判分——召回内容交给人肉眼评估（无 anchor 数据集的兜底）。"""

    def judge(self, question: EvalQuestion, context: MemoryContext) -> JudgeResult:
        return JudgeResult(correct=None, detail="(未判分：PoC 只展示召回，未接 LLM judge)")


class AnchorRecallJudge:
    """基于 PerLTQA ``Memory Anchors`` 的 substring 召回判分。

    每个 anchor 是 PerLTQA 标注的"答案关键 token"；本判分器在召回内容（``context.rendered``）
    里 substring 检索每个 anchor，输出 ``hit / total ∈ [0, 1]``。

    防御：``anchors`` 缺失或全空时返回 ``correct=None``、``score=None``——视为未判分，
    不计入 macro 平均。

    已知偏差：substring 看不出同义改写（如 anchor "提升专业能力" vs 召回 "增强职业素质"
    会被判 0 分）。理由与升级路径详见 012 design §8。
    """

    def judge(self, question: EvalQuestion, context: MemoryContext) -> JudgeResult:
        anchors = question.anchors
        if not anchors:
            return JudgeResult(
                correct=None,
                detail="(无 anchor，跳过判分)",
                score=None,
            )
        text = context.rendered
        hits = [a for a in anchors if a in text]
        misses = [a for a in anchors if a not in text]
        score = len(hits) / len(anchors)
        detail = f"{len(hits)}/{len(anchors)} 命中"
        if misses:
            detail += f"；未命中: {misses}"
        return JudgeResult(
            correct=(score == 1.0),
            detail=detail,
            score=score,
        )
