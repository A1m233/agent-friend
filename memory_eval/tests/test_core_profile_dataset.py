from memory_eval.datasets import load_core_profile_cases
from memory_eval.harness import LayerConstrainedAnchorJudge

from memory import MemoryContext, MemoryItem


def test_core_profile_slice_covers_schema_and_replacement_cases() -> None:
    cases = load_core_profile_cases()
    assert {case.sample_id for case in cases} == {
        "preferred-name-replace",
        "multiple-current-residences",
        "residence-move",
        "agent-nickname",
        "relationship-kind",
        "employer-is-ordinary",
    }
    assert all(case.questions[0].anchors for case in cases)
    assert all(case.questions[0].expected_memory_layer for case in cases)


def test_core_profile_slice_limit_is_deterministic() -> None:
    assert [case.sample_id for case in load_core_profile_cases(limit_samples=2)] == [
        "preferred-name-replace",
        "multiple-current-residences",
    ]


def test_core_profile_judge_does_not_accept_semantic_substitution() -> None:
    question = next(
        case.questions[0]
        for case in load_core_profile_cases()
        if case.sample_id == "relationship-kind"
    )
    context = MemoryContext(
        rendered="用户视 AI 为知心朋友",
        items=[
            MemoryItem(
                text="用户视AI为最信任的知心朋友",
                layer="semantic",
                source_ref="semantic-1",
                score=1.0,
            )
        ],
    )

    result = LayerConstrainedAnchorJudge().judge(question, context)

    assert result.score == 0.0
    assert result.correct is False
    assert "目标层未命中" in result.detail
    assert "禁止层" in result.detail


def test_core_profile_judge_requires_the_declared_core_field() -> None:
    question = load_core_profile_cases()[0].questions[0]
    context = MemoryContext(
        rendered="陈老师",
        items=[
            MemoryItem(
                text="relationship.kind = 陈老师",
                layer="core",
                source_ref="core-1",
                score=1.0,
            )
        ],
    )

    result = LayerConstrainedAnchorJudge().judge(question, context)

    assert result.score == 0.0
    assert result.correct is False
