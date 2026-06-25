from __future__ import annotations

from agent_eval.judge import judge_case
from agent_eval.schema import Case, CaseJudge, Contract, ToolCallTrace, Trace


def _contract(
    expected_behavior: str = "tool_must_be_called",
    expected_tool: str | None = "recall_past_chats",
    required_in_response: str | None = None,
) -> Contract:
    return Contract(
        id="C1",
        name="test-contract",
        expected_behavior=expected_behavior,
        expected_tool=expected_tool,
        required_in_response=required_in_response,
        cases=[],
    )


def _case(judge: CaseJudge | None = None) -> Case:
    return Case(id="C1-01", query="q", facet="", source="", judge=judge)


def _trace(
    *,
    tool_names: list[str] | None = None,
    final_text: str = "",
    error: str | None = None,
) -> Trace:
    calls = [
        ToolCallTrace(name=name, args={}, result_text="", is_error=False)
        for name in (tool_names or [])
    ]
    return Trace(
        case_id="C1-01",
        tool_calls=calls,
        final_text=final_text,
        stop_reason="end_turn",
        error=error,
    )


def test_tool_must_be_called_passes() -> None:
    verdict = judge_case(_case(), _contract(), _trace(tool_names=["recall_past_chats"]))

    assert verdict.passed is True


def test_tool_must_be_called_fails_when_missing() -> None:
    verdict = judge_case(_case(), _contract(), _trace(tool_names=["web_search"]))

    assert verdict.passed is False
    assert "missing recall_past_chats" in verdict.reason


def test_identity_must_hold() -> None:
    contract = _contract(
        expected_behavior="identity_must_hold",
        expected_tool=None,
        required_in_response="我是默默",
    )

    assert judge_case(_case(), contract, _trace(final_text="我是默默。")).passed is True
    assert judge_case(_case(), contract, _trace(final_text="我是 Echo。")).passed is False


def test_case_level_anchor_and_forbidden_terms() -> None:
    case = _case(
        CaseJudge(
            must_mention_any=["杭州", "offer"],
            must_not_mention_any=["上海"],
        )
    )
    contract = _contract()

    ok = judge_case(
        case,
        contract,
        _trace(tool_names=["recall_past_chats"], final_text="你之前说杭州那个 offer 有进展。"),
    )
    assert ok.passed is True

    anchor_miss = judge_case(
        case,
        contract,
        _trace(tool_names=["recall_past_chats"], final_text="我有点想不起来。"),
    )
    assert anchor_miss.passed is False
    assert "no anchor matched" in anchor_miss.reason

    forbidden_hit = judge_case(
        case,
        contract,
        _trace(tool_names=["recall_past_chats"], final_text="杭州那个 offer 是不是要去上海？"),
    )
    assert forbidden_hit.passed is False
    assert "fabrication suspect" in forbidden_hit.reason


def test_runtime_error_fails() -> None:
    verdict = judge_case(_case(), _contract(), _trace(error="LLMProviderError: boom"))

    assert verdict.passed is False
    assert "runtime error" in verdict.reason
