"""AnchorRecallJudge 的 sanity case：验证判分器在人造输入上的行为正确。

不依赖真实 LLM、不触发 ingest，纯构造 ``EvalQuestion`` + ``MemoryContext`` 直接调
``judge.judge(...)``。覆盖三种行为：全命中 / 全不命中 / 部分命中（含浮点严格相等）。
"""

from __future__ import annotations

from memory_eval.datasets import EvalQuestion
from memory_eval.harness import AnchorRecallJudge

from memory import MemoryContext


def _question(anchors: list[str]) -> EvalQuestion:
    return EvalQuestion(
        question="(占位问题)",
        answer="(占位答案)",
        category="dialogues",
        evidence=[],
        anchors=anchors,
    )


def _context(rendered: str) -> MemoryContext:
    # judge 只读 rendered；items 用空列表即可
    return MemoryContext(rendered=rendered, items=[])


def test_anchor_judge_all_hit_full_score() -> None:
    """全部 anchor 都在 rendered 中出现 → score == 1.0、correct == True、detail 不含未命中。"""
    judge = AnchorRecallJudge()
    result = judge.judge(
        _question(["建议", "培训课程", "情绪管理"]),
        _context("AI建议参加培训课程，并学习情绪管理技巧"),
    )
    assert result.score == 1.0
    assert result.correct is True
    assert "未命中" not in result.detail
    assert "3/3" in result.detail


def test_anchor_judge_all_miss_zero_score() -> None:
    """全部 anchor 都不在 rendered 中 → score == 0.0、correct == False、detail 列出全部未命中。"""
    judge = AnchorRecallJudge()
    result = judge.judge(
        _question(["亲密关系建立", "提升专业能力"]),
        _context("用户聊了完全无关的天气话题"),
    )
    assert result.score == 0.0
    assert result.correct is False
    assert "0/2" in result.detail
    assert "亲密关系建立" in result.detail
    assert "提升专业能力" in result.detail


def test_anchor_judge_partial_hit_exact_fraction() -> None:
    """部分命中：4 个 anchor 命中 2 个 → score 严格等于 0.5（不是 approx）。

    严格相等可以抓住 "分子分母搞反" / "<= 误写 <" 等算法回归——0.5 = 2/4 是有理数除法，
    浮点能精确表达，不需要 pytest.approx。
    """
    judge = AnchorRecallJudge()
    result = judge.judge(
        _question(["P", "Q", "R", "S"]),
        _context("文本里只出现了 P 和 R 这两个 anchor"),
    )
    assert result.score == 0.5  # 严格相等
    assert result.correct is False
    assert "2/4" in result.detail
    # 未命中清单包含 Q 和 S，命中清单不出现在 detail 里
    assert "Q" in result.detail
    assert "S" in result.detail


def test_anchor_judge_empty_anchors_skipped() -> None:
    """anchors 为空 → 未判分（score=None / correct=None），不计入 macro。"""
    judge = AnchorRecallJudge()
    result = judge.judge(
        _question([]),
        _context("任意召回内容"),
    )
    assert result.score is None
    assert result.correct is None
    assert "跳过判分" in result.detail
