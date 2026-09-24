from dataclasses import replace
from pathlib import Path

from agent_eval.judge import judge_case
from agent_eval.loader import load_contracts
from agent_eval.schema import ToolCallTrace, Trace


def test_uploaded_file_identity_cases_reject_substitution_and_accept_grounded_results() -> None:
    contract = next(c for c in load_contracts(Path(__file__).parents[1] / "cases") if c.id == "C24")
    for case_id, token in [("C24-11", "a"), ("C24-12", "c")]:
        case = next(c for c in contract.cases if c.id == case_id)
        calls = [
            ToolCallTrace(
                name="session_files", args={"action": "list"}, result_text="files", is_error=False
            ),
            ToolCallTrace(
                name="session_files",
                args={"action": "prepare", "file_ref": "file-" + token * 64},
                result_text="verified" if token == "a" else "missing",
                is_error=token != "a",
            ),
        ]
        good = Trace(
            case_id=case_id,
            tool_calls=calls,
            final_text="已准备好原件副本。" if token == "a" else "原件缺失，请重新上传。",
            stop_reason="end_turn",
        )
        assert judge_case(case, contract, good).passed
        bad = replace(
            good,
            tool_calls=[
                ToolCallTrace(
                    name="exec_command",
                    args={"cmd": "search Documents"},
                    result_text="same name",
                    is_error=False,
                )
            ],
            final_text="已找到你上传的原件。",
        )
        assert not judge_case(case, contract, bad).passed
        assert not judge_case(case, contract, replace(good, tool_calls=[])).passed
