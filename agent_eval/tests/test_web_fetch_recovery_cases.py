import json
from dataclasses import replace
from pathlib import Path

import pytest
from agent.tools.builtin.web_fetch import WebFetchTool
from agent.tools.builtin.web_fetch.provider import (
    ExtractedContent,
    WebContentHttpError,
    WebContentTimeoutError,
)
from agent_eval.judge import judge_case
from agent_eval.loader import load_contracts
from agent_eval.schema import FixtureToolCallResult, ToolCallTrace, Trace

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


@pytest.mark.parametrize(
    ("case_id", "error", "answer", "bad_answer"),
    [
        ("C26-10", WebContentHttpError(404), "发布日期为 2026-08-17。", "网络超时了，稍后再试。"),
        ("C26-11", WebContentHttpError(403), "站点拒绝访问，返回 403。", "页面不存在，返回 404。"),
        ("C26-12", WebContentTimeoutError("fixture"), "请求超时，尚未取得正文。", "页面已被删除。"),
    ],
)
def test_recovery_fixture_matches_production_and_rejects_original_misclassification(
    case_id: str, error: Exception, answer: str, bad_answer: str
) -> None:
    contract = next(c for c in load_contracts(CASES_DIR) if c.id == "C26")
    case = next(c for c in contract.cases if c.id == case_id)

    class Reader:
        name = "fixture"

        def open(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            raise error

    result = WebFetchTool(html_reader=Reader()).invoke(  # type: ignore[arg-type]
        {"url": "https://example.org/source"}
    )
    historical_result = case.current_session_events[-1]
    assert isinstance(historical_result, FixtureToolCallResult)
    assert result.text == historical_result.content
    calls = []
    if case_id == "C26-10":
        calls = [
            ToolCallTrace(name="web_search", args={}, result_text="new address", is_error=False),
            ToolCallTrace(
                name="web_fetch",
                args={"url": "https://example.org/reports/rainfall"},
                result_text=answer,
                is_error=False,
            ),
        ]
    trace = Trace(case_id=case_id, tool_calls=calls, final_text=answer, stop_reason="end_turn")
    assert judge_case(case, contract, trace).passed
    assert not judge_case(case, contract, replace(trace, final_text=bad_answer)).passed
    repeated = ToolCallTrace(
        name="web_fetch",
        args={"url": "https://example.org/old"},
        result_text="failed",
        is_error=True,
    )
    assert not judge_case(case, contract, replace(trace, tool_calls=[*calls, repeated])).passed


def test_recorded_404_recovery_accepts_equivalent_date_without_weakening_recovery() -> None:
    report_path = CASES_DIR.parent / "reports" / "2026-11-04T17-52-36-688f5111-C26-10.json"
    recorded = json.loads(report_path.read_text(encoding="utf-8"))["contracts"][0]["cases"][0]
    contract = next(c for c in load_contracts(CASES_DIR) if c.id == "C26")
    case = next(c for c in contract.cases if c.id == "C26-10")
    assert case.judge is not None
    trace = Trace(
        case_id=case.id,
        tool_calls=[ToolCallTrace(**item) for item in recorded["tool_calls"]],
        final_text=recorded["final_text"],
        stop_reason=recorded["stop_reason"],
    )
    assert recorded["passed"] is False  # Preserve the original machine verdict.
    assert judge_case(case, contract, trace).passed
    for date in ("2026-08-17", "2026年8月17日"):
        equivalent = replace(trace, final_text=trace.final_text.replace("2026 年 8 月 17 日", date))
        assert judge_case(case, contract, equivalent).passed
    wrong_date = replace(
        trace, final_text=trace.final_text.replace("2026 年 8 月 17 日", "2026 年 8 月 18 日")
    )
    assert not judge_case(case, contract, wrong_date).passed
    assert not judge_case(case, contract, replace(trace, tool_calls=[])).passed
    repeated = ToolCallTrace(
        name="web_fetch",
        args={"url": "https://example.org/archive/rainfall-report"},
        result_text="not found",
        is_error=True,
    )
    assert not judge_case(
        case, contract, replace(trace, tool_calls=[repeated, *trace.tool_calls])
    ).passed
