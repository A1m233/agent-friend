"""048 的六类确定性 retrieval 评测契约；不调用外部 LLM。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Pass2Case:
    id: str
    slice_name: str
    query: str
    expected_ids: frozenset[str]
    forbidden_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Pass2Outcome:
    case_id: str
    slice_name: str
    passed: bool
    missing_ids: frozenset[str]
    forbidden_hits: frozenset[str]


@dataclass(frozen=True)
class Pass2Report:
    outcomes: tuple[Pass2Outcome, ...]
    passed: int
    total: int
    slice_pass_rate: dict[str, float]


PASS2_CASES = (
    Pass2Case("identity-core", "identity", "你还记得我吗", frozenset({"semantic:name"})),
    Pass2Case(
        "paraphrase-pet",
        "paraphrase",
        "那个总霸占键盘的小家伙叫什么",
        frozenset({"semantic:pet"}),
    ),
    Pass2Case(
        "temporal-last-chat",
        "temporal",
        "上周我们聊过什么",
        frozenset({"session:last-week"}),
        frozenset({"session:this-week"}),
    ),
    Pass2Case(
        "update-current-job",
        "update",
        "我现在在哪里工作",
        frozenset({"semantic:job-current"}),
        frozenset({"semantic:job-old"}),
    ),
    Pass2Case("abstention-dog", "abstention", "我家的狗叫什么", frozenset()),
    Pass2Case(
        "consistency-shared",
        "consistency",
        "我们之前聊过日语吗",
        frozenset({"episodic:japanese"}),
    ),
)


def evaluate_pass2(
    rankings: Mapping[str, Collection[str]],
    *,
    cases: tuple[Pass2Case, ...] = PASS2_CASES,
) -> Pass2Report:
    """分别报告 retrieval 命中；generation groundedness 由 agent_eval C8 负责。"""
    outcomes: list[Pass2Outcome] = []
    slice_totals: dict[str, int] = {}
    slice_passed: dict[str, int] = {}
    for case in cases:
        actual = frozenset(rankings.get(case.id, ()))
        missing = case.expected_ids - actual
        forbidden = case.forbidden_ids & actual
        if not case.expected_ids and actual:
            forbidden = actual
        passed = not missing and not forbidden
        outcomes.append(
            Pass2Outcome(
                case_id=case.id,
                slice_name=case.slice_name,
                passed=passed,
                missing_ids=missing,
                forbidden_hits=forbidden,
            )
        )
        slice_totals[case.slice_name] = slice_totals.get(case.slice_name, 0) + 1
        slice_passed[case.slice_name] = slice_passed.get(case.slice_name, 0) + int(passed)
    return Pass2Report(
        outcomes=tuple(outcomes),
        passed=sum(outcome.passed for outcome in outcomes),
        total=len(outcomes),
        slice_pass_rate={
            name: slice_passed.get(name, 0) / total for name, total in slice_totals.items()
        },
    )
