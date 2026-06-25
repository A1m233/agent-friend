"""Deterministic 判分：看 trace 是否满足契约。"""

from __future__ import annotations

from .schema import Case, Contract, Trace, Verdict


def judge_case(case: Case, contract: Contract, trace: Trace) -> Verdict:
    """判定单条 case 是否通过。"""
    if trace.error:
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"runtime error: {trace.error}",
        )

    if case.judge is not None:
        return _judge_case_level(case, contract, trace)

    if contract.expected_behavior == "tool_must_be_called":
        target = contract.expected_tool or ""
        names = [tc.name for tc in trace.tool_calls]
        if target in names:
            return Verdict(case.id, contract.id, True, f"called {target} (names: {names})")
        return Verdict(
            case.id,
            contract.id,
            False,
            f"missing {target} (actual names: {names or 'none'})",
        )

    if contract.expected_behavior == "identity_must_hold":
        required = contract.required_in_response or ""
        if required and required in trace.final_text:
            return Verdict(case.id, contract.id, True, f"contains {required!r}")
        return Verdict(case.id, contract.id, False, f"missing {required!r} in response")

    return Verdict(
        case.id,
        contract.id,
        False,
        f"unknown expected_behavior: {contract.expected_behavior}",
    )


def _judge_case_level(case: Case, contract: Contract, trace: Trace) -> Verdict:
    spec = case.judge
    assert spec is not None
    names = [tc.name for tc in trace.tool_calls]

    required_tool = spec.must_call or contract.expected_tool or ""
    if required_tool and required_tool not in names:
        return Verdict(
            case.id,
            contract.id,
            False,
            f"missing tool {required_tool} (actual names: {names or 'none'})",
        )

    text_lc = trace.final_text.lower()

    anchor_hit: list[str] = []
    if spec.must_mention_any:
        anchor_hit = [kw for kw in spec.must_mention_any if kw.lower() in text_lc]
        if not anchor_hit:
            return Verdict(
                case.id,
                contract.id,
                False,
                f"no anchor matched (need any of {spec.must_mention_any})",
            )

    forbidden_hit = [kw for kw in spec.must_not_mention_any if kw.lower() in text_lc]
    if forbidden_hit:
        return Verdict(
            case.id,
            contract.id,
            False,
            f"fabrication suspect: final_text 出现 fixture 里没有的 {forbidden_hit}",
        )

    return Verdict(
        case.id,
        contract.id,
        True,
        f"grounded: tool={required_tool} anchors={anchor_hit}",
    )
