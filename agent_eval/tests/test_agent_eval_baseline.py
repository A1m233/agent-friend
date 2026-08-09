from __future__ import annotations

import json
from datetime import UTC, datetime

from agent_eval.baseline import ProviderRunInfo, ReportRun, ToolRunInfo, write_report
from agent_eval.report import CaseRunResult
from agent_eval.schema import (
    Case,
    CaseSetMeta,
    Contract,
    SkillCatalogEntryObservation,
    SkillCatalogObservation,
    ToolCallTrace,
    Trace,
    Verdict,
)
from agent_eval.selection import Selection


def _contract() -> Contract:
    return Contract(
        id="C4",
        name="fact-fabrication-resistance",
        expected_behavior="tool_must_be_called",
        expected_tool="recall_past_chats",
        required_in_response=None,
        cases=[],
    )


def _result() -> CaseRunResult:
    case = Case(id="C4-07", query="你上次推过的那部诺兰的片叫啥", facet="facet", source="test")
    trace = Trace(
        case_id=case.id,
        tool_calls=[
            ToolCallTrace(
                name="recall_past_chats",
                args={"search_text": "诺兰"},
                result_text="我翻了翻，好像没和你聊过这个。",
                is_error=False,
                duration_seconds=0.01,
            )
        ],
        final_text="我没找到我们之前聊过诺兰。",
        stop_reason="end_turn",
        assistant_segments=["我翻一下之前聊过的。", "我没找到我们之前聊过诺兰。"],
    )
    verdict = Verdict(case_id=case.id, contract_id="C4", passed=True, reason="grounded")
    return CaseRunResult(case=case, trace=trace, verdict=verdict)


def _run() -> ReportRun:
    return ReportRun(
        started_at=datetime(2026, 6, 25, 8, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 6, 25, 8, 1, 30, tzinfo=UTC),
        selection=Selection(contract="C4", limit_cases=1),
        provider=ProviderRunInfo(
            model="deepseek/deepseek-v4-flash",
            api_base=None,
            defaults={"temperature": 0},
            context_window=8192,
        ),
        tools=ToolRunInfo(
            tavily_key_present=False,
            web_search_available=False,
            recall_past_chats_available=True,
        ),
        case_set=CaseSetMeta(
            digest="a" * 64,
            total_contracts=5,
            total_cases=103,
            contract_counts={"C1": 23, "C2": 20, "C3": 20, "C4": 20, "C5": 20},
        ),
        note="schema test",
    )


def _catalog_result() -> CaseRunResult:
    result = _result()
    entry = SkillCatalogEntryObservation(
        name="agent-friend-guide",
        source="builtin",
        id="builtin:agent-friend-guide",
    )
    trace = Trace(
        case_id=result.trace.case_id,
        tool_calls=result.trace.tool_calls,
        final_text="agent-friend-guide 当前可发现。",
        stop_reason="end_turn",
        skill_catalog_observation=SkillCatalogObservation(
            snapshot_entries=[entry],
            snapshot_coverage="complete",
            catalog_fingerprint="catalog",
            render_fingerprint="render",
            request_entries=[entry],
            request_coverage="complete",
            request_content_fingerprint="request",
            request_message_index=1,
            preflight_passed=True,
        ),
    )
    return CaseRunResult(case=result.case, trace=trace, verdict=result.verdict)


def test_write_report_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    contract = _contract()
    path = write_report(
        out_dir=tmp_path / "reports",
        label="C4-limit1",
        contracts_run=[contract],
        results={"C4": [_result()]},
        run=_run(),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 3
    assert path.name.startswith("2026-06-25T08-00-00-")
    assert path.name.endswith("-C4-limit1.json")

    run = payload["run"]
    assert run["duration_seconds"] == 90.0
    assert run["git_commit"]
    assert run["working_tree_dirty"] in (True, False, None)
    assert run["note"] == "schema test"
    assert run["selection"] == {"contract": "C4", "case": None, "limit_cases": 1}
    assert run["provider"]["model"] == "deepseek/deepseek-v4-flash"
    assert run["provider"]["defaults"] == {"temperature": 0}
    assert run["tools"]["web_search_available"] is False
    assert run["case_set"]["total_cases"] == 103

    assert payload["summary"]["total"] == 1
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["pass_rate"] == 1.0

    case = payload["contracts"][0]["cases"][0]
    assert case["id"] == "C4-07"
    assert case["passed"] is True
    assert case["tool_calls"][0]["args"] == {"search_text": "诺兰"}
    assert case["assistant_segments"] == [
        "我翻一下之前聊过的。",
        "我没找到我们之前聊过诺兰。",
    ]
    assert case["skill_catalog_observation"] is None


def test_write_report_serializes_skill_catalog_observation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    contract = _contract()
    path = write_report(
        out_dir=tmp_path / "reports",
        label="C21-01",
        contracts_run=[contract],
        results={"C4": [_catalog_result()]},
        run=_run(),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    observation = payload["contracts"][0]["cases"][0]["skill_catalog_observation"]
    assert observation["preflight_passed"] is True
    assert observation["request_coverage"] == "complete"
    assert observation["request_entries"] == [
        {
            "name": "agent-friend-guide",
            "source": "builtin",
            "id": "builtin:agent-friend-guide",
        }
    ]
