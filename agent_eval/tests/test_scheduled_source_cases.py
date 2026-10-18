from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from agent_eval.judge import judge_case
from agent_eval.loader import _request_source, load_contracts
from agent_eval.schema import ToolCallTrace, Trace

from agent_eval import runner
from llm_providers import LLMStreamEvent, LLMTextDelta, LLMTurnDone, ModelRequest, ProviderSpec


def test_scheduled_source_cases_judge_action_boundary() -> None:
    contract = next(c for c in load_contracts(Path(__file__).parents[1] / "cases") if c.id == "C18")
    cases = {c.id: c for c in contract.cases}
    skipped = Trace(
        case_id="C18-07",
        tool_calls=[],
        final_text="按之前的要求，本次跳过。",
        stop_reason="end_turn",
    )
    call = ToolCallTrace(name="exec_command", args={}, result_text="ok", is_error=False)
    assert judge_case(cases["C18-07"], contract, skipped).passed
    assert not judge_case(
        cases["C18-07"], contract, replace(skipped, tool_calls=[call], final_text="开始执行")
    ).passed
    for case_id in ("C18-08", "C18-09"):
        trace = replace(skipped, case_id=case_id, tool_calls=[call], final_text="检查完成。")
        assert judge_case(cases[case_id], contract, trace).passed
        assert not judge_case(cases[case_id], contract, replace(trace, tool_calls=[])).passed


def test_runner_delivers_host_source_through_production_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = next(c for c in load_contracts(Path(__file__).parents[1] / "cases") if c.id == "C18")
    case = next(c for c in contract.cases if c.id == "C18-07")
    received = []

    class LocalModel:
        context_window = 128000

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            received.append(request.messages)
            yield LLMTextDelta(text="本次跳过。")
            yield LLMTurnDone(stop_reason="end_turn")

    monkeypatch.setattr(runner, "LLMClient", lambda spec: LocalModel())
    trace = runner.run_case(case, ProviderSpec(model="openai/test", api_key="fixture"))
    assert trace.error is None
    source = [m for m in received[0] if "<agent_friend_request_source" in str(m.get("content"))]
    assert len(source) == 1 and source[0]["content"].endswith(case.query)
    assert '"task_id": "fixture-sync"' in source[0]["content"]


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "manual"},
        {"kind": "scheduled_task", "trigger_kind": "invented"},
        {"kind": "scheduled_task", "scheduled_at_utc": "2026-10-18T12:00:00"},
        {"kind": "scheduled_task", "other": "x"},
    ],
)
def test_invalid_fixture_source_is_rejected(value: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        _request_source(value, label="test")
