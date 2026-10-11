from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from agent.tools import Tool, ToolRegistry
from agent.tools.builtin.view_image.tool import ViewImageTool
from agent_eval.judge import judge_case
from agent_eval.loader import load_contracts
from agent_eval.runner import _FixtureTool
from agent_eval.schema import Case, Contract, FakeTool, ToolCallTrace, Trace

CASES = Path(__file__).resolve().parents[1] / "cases"


def _case(case_id: str) -> tuple[Contract, Case]:
    contract = next(c for c in load_contracts(CASES) if c.id == case_id.split("-")[0])
    return contract, next(c for c in contract.cases if c.id == case_id)


def _call(name: str, args: dict[str, Any], *, error: bool = False) -> ToolCallTrace:
    return ToolCallTrace(name=name, args=args, result_text="fixture", is_error=error)


def test_screenshot_scope_judge_rejects_missing_image_and_identifier_confusion() -> None:
    contract, case = _case("C24-13")
    calls = [
        _call("skill_read", {"skill_id": "builtin/screenshot"}),
        _call(
            "exec_command",
            {
                "skill": {"entry": "scripts/take_screenshot.ps1"},
                "access": {"filesystem": "read_write"},
            },
        ),
        _call(
            "view_image",
            {
                "filesystem_scope_id": "scope_capture_current",
                "path": "desktop-check.png",
            },
        ),
    ]
    trace = Trace(case.id, calls, "截图已读取。", "end_turn")
    assert judge_case(case, contract, trace).passed
    # The original incident completed no image read after an unknown command outcome.
    bad = replace(trace, tool_calls=[calls[0], replace(calls[1], is_error=True)])
    assert not judge_case(case, contract, bad).passed
    for scope in ("builtin/screenshot", "scope_from_previous_turn"):
        wrong = replace(calls[2], args={"filesystem_scope_id": scope, "path": "desktop-check.png"})
        assert not judge_case(case, contract, replace(trace, tool_calls=[*calls[:2], wrong])).passed
    absolute = replace(
        calls[2],
        args={
            "filesystem_scope_id": "scope_capture_current",
            "path": "C:/fixture/capture/desktop-check.png",
        },
    )
    assert not judge_case(case, contract, replace(trace, tool_calls=[*calls[:2], absolute])).passed
    redundant = [*calls[:2], _call("request_filesystem_access", {}), calls[2]]
    assert not judge_case(case, contract, replace(trace, tool_calls=redundant)).passed


def test_unknown_screenshot_control_stops_without_replay_or_false_image() -> None:
    contract, case = _case("C17-07")
    command = _call("exec_command", {}, error=True)
    trace = Trace(case.id, [command], "启动结果未知，目前无法确认截图是否生成。", "end_turn")
    assert judge_case(case, contract, trace).passed
    for extra in (command, _call("host_process_start", {}), _call("view_image", {})):
        assert not judge_case(case, contract, replace(trace, tool_calls=[command, extra])).passed
    assert not judge_case(case, contract, replace(trace, final_text="截图已经成功生成。")).passed


def test_ordinary_command_holdout_still_accepts_workdir() -> None:
    contract, case = _case("C24-03")
    trace = Trace(case.id, [_call("exec_command", {"workdir": "C:\\fixture"})], "ready", "end_turn")
    assert judge_case(case, contract, trace).passed


def test_fake_image_tool_preserves_production_scope_contract() -> None:
    tool = _FixtureTool(FakeTool(name="view_image", result_text="fixture metadata"))
    assert tool.description == ViewImageTool.description
    assert tool.parameters_schema == ViewImageTool.parameters_schema
    registry = ToolRegistry([cast(Tool, tool)])
    wire = registry.to_openai_tools()[0]["function"]
    assert wire["name"] == "view_image"
    assert wire["parameters"]["additionalProperties"] is False
    assert "__agent_friend_image_input_supported" not in wire["parameters"]["properties"]
    assert (
        registry.validate(
            "view_image",
            {
                "filesystem_scope_id": "scope_capture_current",
                "path": "desktop-check.png",
            },
        )
        is None
    )
    assert (
        registry.validate("view_image", {"filesystem_scope_id": "scope_capture_current"})
        is not None
    )
    assert (
        "output_directory.filesystem_scope_id"
        in tool.parameters_schema["properties"]["filesystem_scope_id"]["description"]
    )
    # Metadata-only fixture must not pretend to supply image pixels.
    assert tool.invoke({"filesystem_scope_id": "scope", "path": "capture.png"}).images == ()
