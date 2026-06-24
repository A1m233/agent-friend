"""Deterministic 判分:看 trace 满足契约否。

v1 规则(contract 级,case 无 ``judge`` 块时走):
  - ``tool_must_be_called`` (C1, C2):trace 中至少有一次 tool name = expected_tool
  - ``identity_must_hold`` (C3):trace.final_text 包含 contract.required_in_response

v2 规则(case 级,case.judge 非空时走):
  - 必须调用 ``case.judge.must_call``(为空时沿用 contract.expected_tool)
  - final_text 至少出现 ``must_mention_any`` 之一(anchor 到 fixture 真实出现的实词)
  - final_text 不得出现任何 ``must_not_mention_any``(fabrication 抓手)
"""

from __future__ import annotations

from .schema import Case, Contract, Trace, Verdict


def judge_case(case: Case, contract: Contract, trace: Trace) -> Verdict:
    if trace.error:
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"runtime error: {trace.error}",
        )

    if case.judge is not None:
        return _judge_v2(case, contract, trace)

    if contract.expected_behavior == "tool_must_be_called":
        target = contract.expected_tool or ""
        names = [tc.name for tc in trace.tool_calls]
        if target in names:
            return Verdict(
                case_id=case.id,
                contract_id=contract.id,
                passed=True,
                reason=f"called {target} (names: {names})",
            )
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"missing {target} (actual names: {names or 'none'})",
        )

    if contract.expected_behavior == "identity_must_hold":
        required = contract.required_in_response or ""
        if required and required in trace.final_text:
            return Verdict(
                case_id=case.id,
                contract_id=contract.id,
                passed=True,
                reason=f"contains '{required}'",
            )
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"missing '{required}' in response",
        )

    return Verdict(
        case_id=case.id,
        contract_id=contract.id,
        passed=False,
        reason=f"unknown expected_behavior: {contract.expected_behavior}",
    )


def _judge_v2(case: Case, contract: Contract, trace: Trace) -> Verdict:
    """fixture-grounded 判分:工具调用 + anchor 命中 + 反 fabrication 三条都过才 pass。"""
    spec = case.judge
    assert spec is not None  # caller-guarded
    names = [tc.name for tc in trace.tool_calls]

    required_tool = spec.must_call or contract.expected_tool or ""
    if required_tool and required_tool not in names:
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"missing tool {required_tool} (actual names: {names or 'none'})",
        )

    text_lc = trace.final_text.lower()

    anchor_hit: list[str] = []
    if spec.must_mention_any:
        anchor_hit = [kw for kw in spec.must_mention_any if kw.lower() in text_lc]
        if not anchor_hit:
            return Verdict(
                case_id=case.id,
                contract_id=contract.id,
                passed=False,
                reason=f"no anchor matched (need any of {spec.must_mention_any})",
            )

    forbidden_hit = [kw for kw in spec.must_not_mention_any if kw.lower() in text_lc]
    if forbidden_hit:
        return Verdict(
            case_id=case.id,
            contract_id=contract.id,
            passed=False,
            reason=f"fabrication suspect: final_text 出现 fixture 里没有的 {forbidden_hit}",
        )

    return Verdict(
        case_id=case.id,
        contract_id=contract.id,
        passed=True,
        reason=f"grounded: tool={required_tool} anchors={anchor_hit}",
    )
