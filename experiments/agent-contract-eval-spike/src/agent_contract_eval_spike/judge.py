"""Deterministic 判分:看 trace 满足契约否。

v1 规则:
  - ``tool_must_be_called`` (C1, C2):trace 中至少有一次 tool name = expected_tool
  - ``identity_must_hold`` (C3):trace.final_text 包含 contract.required_in_response
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
