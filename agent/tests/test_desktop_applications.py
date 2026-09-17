from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import ApprovalBroker
from agent.approvals.risk import CatastrophicActionPolicy
from agent.command_runtime.models import CommandApprovalDecision
from agent.command_runtime.tools import (
    COMMAND_EXECUTION_CHECKPOINT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
)
from agent.desktop_applications import LaunchDesktopApplicationTool
from agent.sessions.tool_execution_evidence import (
    ToolExecutionEvidenceStore,
    render_interrupted_evidence,
)
from agent.tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from agent.tools.registry import ToolRegistry


class FakeLauncher:
    error: str | None = None

    def __init__(self, status: str = "accepted") -> None:
        self.calls: list[dict[str, object]] = []
        self.status = status

    def validate(self) -> str | None:
        return self.error

    def launch(self, request: dict[str, object], control: ToolInvocationControl) -> dict[str, Any]:
        self.calls.append(request)
        return {"status": self.status}


@pytest.fixture
def harness(tmp_path: Path) -> tuple[Any, ...]:
    executable = tmp_path / "Some Application.exe"
    executable.write_bytes(b"fixture")
    broker = ApprovalBroker(risk_policy=CatastrophicActionPolicy())
    launcher = FakeLauncher()
    store = ToolExecutionEvidenceStore(tmp_path / "evidence")
    tool = LaunchDesktopApplicationTool(launcher, broker, store)  # type: ignore[arg-type]
    checkpoints = []
    args = {
        "executable_path": str(executable),
        "reason": "打开已确认的应用",
        "arguments": ["", 'a"b', "a b", "end\\"],
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_SESSION_ID_ARG: "session",
        COMMAND_RUN_ID_ARG: "run",
        COMMAND_TOOL_CALL_ID_ARG: "call",
        COMMAND_EXECUTION_CHECKPOINT_ARG: lambda *values: checkpoints.append(values),
    }
    return tool, launcher, broker, store, args, checkpoints


def approve(broker: ApprovalBroker) -> None:
    def listener(request: dict[str, object]) -> None:
        if request["status"] == "pending":
            broker.decide(str(request["request_id"]), CommandApprovalDecision(True))

    broker.add_listener(listener)


def test_acceptance_is_not_readiness_and_duplicate_does_not_launch(
    harness: tuple[Any, ...],
) -> None:
    tool, launcher, broker, store, args, checkpoints = harness
    approve(broker)
    result = tool.invoke(args)
    value = json.loads(result.text)
    assert not result.is_error
    assert value["status"] == "accepted"
    assert value["readiness"] == "not_verified"
    assert value["lifecycle"] == "user_desktop"
    assert "process_id" not in value
    assert launcher.calls[0]["arguments"] == args["arguments"]
    assert len(checkpoints) == 1
    assert json.loads(tool.invoke(args).text)["launch_id"] == value["launch_id"]
    assert len(launcher.calls) == 1
    evidence = store.load("session", value["evidence_ref"])
    assert evidence.kind == "application_launch"
    rendered = render_interrupted_evidence(evidence)
    assert rendered is not None
    assert json.loads(rendered)["status"] == "accepted"


def test_unknown_is_durable_and_never_automatically_replayed(harness: tuple[Any, ...]) -> None:
    tool, launcher, broker, store, args, _ = harness
    launcher.status = "unknown"
    approve(broker)
    result = tool.invoke(args)
    assert result.is_error
    value = json.loads(result.text)
    assert value["status"] == "unknown"
    reconstructed = LaunchDesktopApplicationTool(launcher, broker, store)
    assert json.loads(reconstructed.invoke(args).text)["status"] == "unknown"
    assert len(launcher.calls) == 1


def test_approval_denial_never_dispatches(harness: tuple[Any, ...]) -> None:
    tool, launcher, broker, _, args, checkpoints = harness
    broker.add_listener(
        lambda r: (
            broker.decide(r["request_id"], CommandApprovalDecision(False))
            if r["status"] == "pending"
            else None
        )
    )
    assert json.loads(tool.invoke(args).text)["status"] == "denied"
    assert not launcher.calls and not checkpoints


def test_target_changed_after_approval_is_not_launched(harness: tuple[Any, ...]) -> None:
    tool, launcher, broker, _, args, _ = harness

    def decide(request: dict[str, Any]) -> None:
        if request["status"] == "pending":
            Path(args["executable_path"]).write_bytes(b"changed executable")
            broker.decide(request["request_id"], CommandApprovalDecision(True))

    broker.add_listener(decide)
    assert json.loads(tool.invoke(args).text)["error_code"] == "approval_target_changed"
    assert not launcher.calls


@pytest.mark.parametrize("surface", ["im", "cli", ""])
def test_non_desktop_surface_fails_without_approval(harness: tuple[Any, ...], surface: str) -> None:
    tool, launcher, _, _, args, _ = harness
    args[COMMAND_SURFACE_ARG] = surface
    assert json.loads(tool.invoke(args).text)["error_code"] == "approval_surface_unavailable"
    assert not launcher.calls


def test_unavailable_helper_does_not_fallback(harness: tuple[Any, ...]) -> None:
    tool, launcher, _, _, args, _ = harness
    launcher.error = "launcher_unavailable"
    assert json.loads(tool.invoke(args).text)["error_code"] == "launcher_unavailable"
    assert not launcher.calls


def test_actual_registry_schema_rejects_shell_escape_hatches(harness: tuple[Any, ...]) -> None:
    tool, _, _, _, _, _ = harness
    from jsonschema import Draft7Validator

    registry = ToolRegistry([tool])
    schema = registry.get(tool.name).parameters_schema
    validator = Draft7Validator(schema)
    assert list(
        validator.iter_errors({"executable_path": "app.exe", "reason": "open", "detach": True})
    )
    assert list(
        validator.iter_errors(
            {"executable_path": "app.exe", "reason": "open", "arguments": "shell command"}
        )
    )


def test_application_approval_exposes_actual_application_and_lifetime(
    harness: tuple[Any, ...],
) -> None:
    tool, _, broker, _, args, _ = harness
    observed = []
    broker.add_listener(lambda request: observed.append(request))
    approve(broker)
    tool.invoke(args)
    request = observed[0]
    assert request["kind"] == "desktop_application"
    assert request["details"]["executable_path"] == args["executable_path"]
    assert request["details"]["lifecycle"] == "user_desktop"
    assert "mode" not in request["details"]


def test_cancel_in_approval_does_not_launch(harness: tuple[Any, ...]) -> None:
    tool, launcher, broker, _, args, _ = harness
    control = ToolInvocationControl()
    args[TOOL_INVOCATION_CONTROL_ARG] = control
    broker.add_listener(
        lambda r: control.cancel("user_cancelled") if r["status"] == "pending" else None
    )
    assert json.loads(tool.invoke(args).text)["status"] == "cancelled"
    assert not launcher.calls


def test_checkpoint_failure_never_dispatches(harness: tuple[Any, ...]) -> None:
    tool, launcher, broker, _, args, _ = harness
    approve(broker)
    del args[COMMAND_EXECUTION_CHECKPOINT_ARG]
    assert json.loads(tool.invoke(args).text)["error_code"] == "checkpoint_unavailable"
    assert not launcher.calls


def test_final_evidence_failure_reports_unknown_without_replay(
    harness: tuple[Any, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.sessions.tool_execution_evidence import ToolExecutionEvidenceError

    tool, launcher, broker, store, args, _ = harness
    approve(broker)
    original = store.write

    def write(evidence: Any) -> None:
        if evidence.process_status == "accepted":
            raise ToolExecutionEvidenceError("disk full")
        original(evidence)

    monkeypatch.setattr(store, "write", write)
    assert json.loads(tool.invoke(args).text)["status"] == "unknown"
    assert json.loads(tool.invoke(args).text)["status"] == "unknown"
    assert len(launcher.calls) == 1


def test_adding_file_in_working_directory_does_not_invalidate_approval(
    harness: tuple[Any, ...],
) -> None:
    tool, launcher, broker, _, args, _ = harness

    def decide(request: dict[str, Any]) -> None:
        if request["status"] == "pending":
            Path(args["executable_path"]).with_name("unrelated.txt").write_text("new file")
            broker.decide(request["request_id"], CommandApprovalDecision(True))

    broker.add_listener(decide)
    assert json.loads(tool.invoke(args).text)["status"] == "accepted"
    assert len(launcher.calls) == 1
