from dataclasses import replace
from pathlib import Path

from agent_eval.judge import judge_case
from agent_eval.loader import load_contracts
from agent_eval.schema import ToolCallTrace, Trace

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


def test_original_call_recovery_requires_evidence_and_disallows_replay() -> None:
    contract = next(c for c in load_contracts(CASES_DIR) if c.id == "C18")
    case = next(c for c in contract.cases if c.id == "C18-06")
    calls = [
        ToolCallTrace(
            name="inspect_tool_calls",
            args={"mode": mode},
            result_text="historical data",
            is_error=False,
        )
        for mode in ("search", "read")
    ]
    trace = Trace(
        case_id=case.id,
        tool_calls=calls,
        final_text="原调用的账号是 fixture-heron，profile 是 fixture-lake。",
        stop_reason="end_turn",
    )
    assert judge_case(case, contract, trace).passed
    trace = replace(trace, tool_calls=[])
    assert not judge_case(case, contract, trace).passed
    trace = replace(
        trace,
        tool_calls=[
            *calls,
            ToolCallTrace(name="exec_command", args={}, result_text="ok", is_error=False),
        ],
    )
    assert not judge_case(case, contract, trace).passed


def test_unknown_receipt_cannot_become_known_execution() -> None:
    contract = next(c for c in load_contracts(CASES_DIR) if c.id == "C24")
    case = next(c for c in contract.cases if c.id == "C24-10")
    calls = [
        ToolCallTrace(
            name="inspect_tool_calls",
            args={"mode": "search"},
            result_text='{"state":"unknown"}',
            is_error=False,
        )
    ]
    trace = Trace(
        case_id=case.id,
        tool_calls=calls,
        final_text="历史中缺少结果，无法确认执行状态。",
        stop_reason="end_turn",
    )
    assert judge_case(case, contract, trace).passed
    trace = replace(trace, final_text="没有执行，我已经替你重新启动了。")
    assert not judge_case(case, contract, trace).passed
    trace = replace(trace, final_text="执行状态未知，我替你重新启动。")
    trace = replace(
        trace,
        tool_calls=[
            *calls,
            ToolCallTrace(name="exec_command", args={}, result_text="ok", is_error=False),
        ],
    )
    assert not judge_case(case, contract, trace).passed
