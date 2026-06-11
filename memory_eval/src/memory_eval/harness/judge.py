"""判分扩展点（PoC：仅 Protocol + Noop）。

真正的召回质量评分需要 **LLM-as-judge**：把召回 context + question 交给 LLM 生成答案，
再与 ground-truth answer 比对。PoC 先不接，只把召回内容展示给人看——避免在 ingest 的
抽取之外再引入第二处真实 LLM 调用。

未来接 LLM judge 时，新增一个实现 :class:`Judge` 协议的类即可，runner / report 不动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from memory import MemoryContext
    from memory_eval.datasets import EvalQuestion

__all__ = ["Judge", "JudgeResult", "NoopJudge"]


@dataclass(frozen=True)
class JudgeResult:
    """单题判分结果。

    Attributes:
        correct: 是否答对；``None`` 表示未判分（如 :class:`NoopJudge`）。
        detail: 说明文本（judge 的理由 / 生成答案 / 占位说明）。
    """

    correct: bool | None
    detail: str


class Judge(Protocol):
    """判分协议：给定问题 + 召回上下文，产出 :class:`JudgeResult`。"""

    def judge(self, question: EvalQuestion, context: MemoryContext) -> JudgeResult: ...


class NoopJudge:
    """不判分——召回内容交给人肉眼评估（PoC 默认）。"""

    def judge(self, question: EvalQuestion, context: MemoryContext) -> JudgeResult:
        return JudgeResult(correct=None, detail="(未判分：PoC 只展示召回，未接 LLM judge)")
