from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from agent.approvals import ApprovalBroker
from agent.command_runtime import (
    CommandRuntime,
    CommandRuntimeError,
    ExecCommandTool,
    PendingCommandApproval,
)
from agent.command_runtime.models import CommandMode, CommandProcessSnapshot
from agent.command_runtime.runtime import windows_elevated_setup_command
from agent.command_runtime.skill_snapshot import prepare_skill_snapshot
from agent.command_runtime.tools import (
    COMMAND_RUN_CONTEXT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SKILL_SNAPSHOT_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
    MAX_RESULT_OUTPUT_BYTES,
    WriteStdinTool,
    _bounded_process_result,
)
from agent.skills.models import (
    ActivatedSkill,
    RenderedSkillCatalog,
    SkillCatalogSnapshot,
    SkillRecord,
    SkillTurnSnapshot,
)
from agent.tool_runtime import ToolRunContext

FIXTURE = Path(__file__).parent / "fixtures" / "fake_exec_server.py"


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[CommandRuntime]:
    value = CommandRuntime(
        None,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        exec_server_command=[sys.executable, str(FIXTURE)],
        output_tail_bytes=32,
    )
    yield value
    value.close()


def _start(
    runtime: CommandRuntime, command: str, *, mode: CommandMode = "buffered"
) -> CommandProcessSnapshot:
    return runtime.start_process(
        argv=("shell", "-c", command),
        workdir=FIXTURE.parent,
        env={},
        mode=mode,
        session_id="session-a",
        run_id="run-a",
        tool_call_id="call-a",
        sandbox={"permissions": {"type": "managed"}},
    )


@pytest.mark.parametrize(
    ("codex_home", "execution_root"),
    (
        (Path("runtime"), Path("runtime")),
        (Path("runtime"), Path("runtime/execution")),
        (Path("runtime/codex-home"), Path("runtime")),
    ),
)
def test_runtime_rejects_overlapping_control_and_execution_roots(
    tmp_path: Path, codex_home: Path, execution_root: Path
) -> None:
    with pytest.raises(ValueError, match="separate, non-nested"):
        CommandRuntime(
            None,
            codex_home=tmp_path / codex_home,
            execution_root=tmp_path / execution_root,
            exec_server_command=[sys.executable, str(FIXTURE)],
        )


def test_runtime_uses_explicit_execution_root(tmp_path: Path) -> None:
    runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "command-runtime" / "codex-home",
        execution_root=tmp_path / "command-runtime" / "execution",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )
    try:
        assert runtime.execution_root == (tmp_path / "command-runtime" / "execution").resolve()
    finally:
        runtime.close()


def test_buffered_process_collects_terminal_output(runtime: CommandRuntime) -> None:
    started = _start(runtime, "fake:buffered")
    finished = runtime.wait(
        started.process_id,
        session_id="session-a",
        run_id="run-a",
        timeout_seconds=2,
    )
    assert finished.status == "exited"
    assert finished.exit_code == 0
    assert finished.stdout_tail == b"buffered output"
    assert finished.sandbox_type == "windows"


def test_streaming_process_accepts_input_and_is_run_scoped(runtime: CommandRuntime) -> None:
    started = _start(runtime, "fake:streaming", mode="streaming")
    with pytest.raises(CommandRuntimeError, match="another run"):
        runtime.write(started.process_id, b"hello", session_id="session-a", run_id="run-other")
    runtime.write(started.process_id, b"hello", session_id="session-a", run_id="run-a")
    finished = runtime.wait(
        started.process_id,
        session_id="session-a",
        run_id="run-a",
        timeout_seconds=2,
    )
    assert finished.stdout_tail == b"input:hello"


def test_missing_sandbox_confirmation_fails_closed(runtime: CommandRuntime) -> None:
    with pytest.raises(CommandRuntimeError) as raised:
        _start(runtime, "fake:none-sandbox")
    assert raised.value.code == "sandbox_unavailable"


def test_output_tail_is_bounded(runtime: CommandRuntime) -> None:
    started = _start(runtime, "fake:large")
    finished = runtime.wait(
        started.process_id,
        session_id="session-a",
        run_id="run-a",
        timeout_seconds=2,
    )
    assert finished.stdout_tail == b"x" * 32
    assert finished.dropped_bytes == 96


def test_model_result_has_a_smaller_total_output_budget() -> None:
    snapshot = CommandProcessSnapshot(
        process_id="process",
        session_id="session",
        run_id="run",
        tool_call_id="call",
        mode="buffered",
        stdout_tail=b"a" * MAX_RESULT_OUTPUT_BYTES,
        stderr_tail=b"b" * MAX_RESULT_OUTPUT_BYTES,
        pty_tail=b"c" * MAX_RESULT_OUTPUT_BYTES,
    )
    payload = _bounded_process_result(snapshot)
    retained = sum(len(str(payload[name]).encode("utf-8")) for name in ("stdout", "stderr", "pty"))
    assert retained <= MAX_RESULT_OUTPUT_BYTES
    assert payload["dropped_bytes"] == MAX_RESULT_OUTPUT_BYTES * 3 - retained


def test_child_environment_does_not_inherit_credentials(runtime: CommandRuntime) -> None:
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        base_env={
            "PATH": "safe-path",
            "TEMP": "host-temp",
            "OPENAI_API_KEY": "secret",
            "HTTP_PROXY": "http://credential@proxy",
            "AGENT_FRIEND_INTERNAL_TOKEN": "secret",
        },
    )
    child = tool._child_env(runtime.execution_root / "temp")
    assert child["PATH"] == "safe-path"
    assert child["TEMP"].endswith("temp")
    assert "OPENAI_API_KEY" not in child
    assert "HTTP_PROXY" not in child
    assert "AGENT_FRIEND_INTERNAL_TOKEN" not in child


def test_windows_setup_environment_keeps_os_paths_but_not_credentials(tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        base_env={
            "SystemRoot": "C:\\Windows",
            "USERPROFILE": "C:\\Users\\fixture",
            "APPDATA": "C:\\Users\\fixture\\AppData\\Roaming",
            "OPENAI_API_KEY": "secret",
            "HTTP_PROXY": "http://credential@proxy",
            "AGENT_FRIEND_INTERNAL_TOKEN": "secret",
        },
    )
    setup_env = runtime._windows_setup_env()
    assert setup_env["SystemRoot"] == "C:\\Windows"
    assert setup_env["USERPROFILE"] == "C:\\Users\\fixture"
    assert setup_env["APPDATA"].endswith("Roaming")
    assert setup_env["CODEX_HOME"].endswith("codex-home")
    assert "OPENAI_API_KEY" not in setup_env
    assert "HTTP_PROXY" not in setup_env
    assert "AGENT_FRIEND_INTERNAL_TOKEN" not in setup_env


def test_output_notifications_are_coalesced(runtime: CommandRuntime) -> None:
    published: list[dict[str, object]] = []
    runtime.add_listener(published.append)
    started = _start(runtime, "fake:streaming", mode="streaming")
    assert len(published) == 1
    for seq in range(1, 101):
        runtime._handle_notification(
            "process/output",
            {
                "processId": started.process_id,
                "stream": "stdout",
                "seq": seq,
                "chunk": base64.b64encode(b"x").decode("ascii"),
            },
        )
    assert len(published) == 1
    time.sleep(0.3)
    assert len(published) == 2
    assert published[-1]["output_seq"] == 100


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_command_tool_requires_explicit_windows_setup_before_approval(tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
    )
    broker = ApprovalBroker()
    result = ExecCommandTool(runtime, broker).invoke(
        {
            COMMAND_SURFACE_ARG: "desktop_chat",
        }
    )
    payload = json.loads(result.text)
    assert payload["error"] == "windows_sandbox_setup_required"
    assert broker.pending(kind="command") == ()


def test_windows_setup_command_elevates_only_the_explicit_codex_setup(tmp_path: Path) -> None:
    executable = tmp_path / "product codex" / "codex.exe"
    codex_home = tmp_path / "product home"
    command = windows_elevated_setup_command(
        executable,
        codex_home,
        {"SystemRoot": "C:\\Windows"},
    )
    parent = base64.b64decode(command[-1]).decode("utf-16-le")
    assert "-Verb RunAs" in parent
    assert "-WindowStyle Hidden" in parent
    encoded_child = re.search(r"'-EncodedCommand','([^']+)'", parent)
    assert encoded_child is not None
    child = base64.b64decode(encoded_child.group(1)).decode("utf-16-le")
    assert f"$env:CODEX_HOME='{codex_home}'" in child
    assert f"& '{executable}' sandbox setup --elevated --current-user" in child
    assert "exec-server" not in child


def test_command_tool_uses_shared_approval_and_buffered_semantics(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(runtime, broker)
    context = ToolRunContext.create("")
    result_box = []
    args = {
        "cmd": "fake:buffered",
        "workdir": str(tmp_path.resolve()),
        "reason": "test the governed command",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
    }

    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)))
    thread.start()
    request = _wait_for_request(broker)
    from agent.command_runtime import CommandApprovalDecision

    assert broker.decide(
        request.request_id,
        CommandApprovalDecision(allow=True),
        session_id="session-a",
        run_id="run-a",
        expected_kind="command",
    )
    thread.join(timeout=3)
    assert not thread.is_alive()
    payload = json.loads(result_box[0].text)
    assert payload["status"] == "exited"
    assert payload["stdout"] == "buffered output"


def test_streaming_handle_is_closed_with_tool_run(runtime: CommandRuntime, tmp_path: Path) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(runtime, broker)
    context = ToolRunContext.create("")
    args = {
        "cmd": "fake:streaming",
        "workdir": str(tmp_path.resolve()),
        "mode": "streaming",
        "reason": "test interactive process",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
    }
    result_box = []
    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)))
    thread.start()
    request = _wait_for_request(broker)
    from agent.command_runtime import CommandApprovalDecision

    broker.decide(request.request_id, CommandApprovalDecision(allow=True))
    thread.join(timeout=3)
    process_id = json.loads(result_box[0].text)["process_id"]
    assert context.owns_command_handle(process_id)
    context.close_commands()
    snapshot = runtime.wait(
        process_id,
        session_id="session-a",
        run_id="run-a",
        timeout_seconds=1,
    )
    assert snapshot.status == "cancelled"


def test_write_stdin_rejects_handle_from_other_context(runtime: CommandRuntime) -> None:
    started = _start(runtime, "fake:streaming", mode="streaming")
    tool = WriteStdinTool(runtime)
    result = tool.invoke(
        {
            "process_id": started.process_id,
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
        }
    )
    assert result.is_error
    assert json.loads(result.text)["error"] == "invalid_arguments"


def test_skill_package_is_snapshotted_once_and_cleaned_with_run(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    package = tmp_path / "skill"
    script = package / "scripts" / "run.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('first')", encoding="utf-8")
    activation = _activation(package)
    context = ToolRunContext.create("")
    first = prepare_skill_snapshot(
        context,
        activation,
        entry="scripts/run.py",
        execution_root=runtime.execution_root / "skill-test",
    )
    script.write_text("print('second')", encoding="utf-8")
    second = prepare_skill_snapshot(
        context,
        activation,
        entry="scripts/run.py",
        execution_root=runtime.execution_root / "skill-test",
    )
    assert first.snapshot_root == second.snapshot_root
    assert first.entry_path.read_text(encoding="utf-8") == "print('first')"
    context.close_commands()
    assert not first.snapshot_root.exists()


def test_skill_paths_require_structured_binding(runtime: CommandRuntime, tmp_path: Path) -> None:
    package = tmp_path / "skill"
    package.mkdir()
    (package / "run.py").write_text("print('ok')", encoding="utf-8")
    tool = ExecCommandTool(runtime, ApprovalBroker())
    result = tool.invoke(
        {
            "cmd": "python run.py",
            "workdir": str(package),
            "reason": "run activated helper",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
            COMMAND_SKILL_SNAPSHOT_ARG: _turn_snapshot(_activation(package)),
        }
    )
    assert result.is_error
    assert json.loads(result.text)["error"] == "skill_binding_required"


def test_skill_binding_approval_contains_content_fingerprint(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    package = tmp_path / "skill"
    package.mkdir()
    (package / "run.py").write_text("print('ok')", encoding="utf-8")
    activation = _activation(package)
    broker = ApprovalBroker()
    tool = ExecCommandTool(runtime, broker)
    context = ToolRunContext.create("")
    result_box = []
    args = {
        "cmd": "python run.py",
        "workdir": str(package),
        "skill": {"id": activation.record.id, "entry": "run.py"},
        "reason": "run activated helper",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
        COMMAND_SKILL_SNAPSHOT_ARG: _turn_snapshot(activation),
    }
    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)))
    thread.start()
    request = _wait_for_request(broker)
    assert request.details.skill is not None
    assert request.details.skill.skill_id == activation.record.id
    assert len(request.details.skill.fingerprint) == 64
    snapshots = tuple(runtime.execution_root.glob("run-*/skills/skill-*"))
    assert len(snapshots) == 1
    assert snapshots[0].is_relative_to(runtime.execution_root)
    assert not snapshots[0].is_relative_to((tmp_path / "codex-home").resolve())
    from agent.command_runtime import CommandApprovalDecision

    broker.decide(request.request_id, CommandApprovalDecision(allow=False))
    thread.join(timeout=3)
    context.close_commands()
    assert json.loads(result_box[0].text)["status"] == "denied"


def test_approved_workdir_replacement_is_rejected_before_start(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    broker = ApprovalBroker()
    tool = ExecCommandTool(runtime, broker)
    context = ToolRunContext.create("")
    result_box = []
    args = {
        "cmd": "fake:buffered",
        "workdir": str(workdir),
        "reason": "test path identity",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
    }
    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)))
    thread.start()
    request = _wait_for_request(broker)
    workdir.rmdir()
    workdir.mkdir()
    from agent.command_runtime import CommandApprovalDecision

    broker.decide(request.request_id, CommandApprovalDecision(allow=True))
    thread.join(timeout=3)
    assert json.loads(result_box[0].text)["error"] == "path_changed_before_start"
    context.close_commands()


def test_linked_workdir_is_rejected(runtime: CommandRuntime, tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        os.symlink(target, linked, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    tool = ExecCommandTool(runtime, ApprovalBroker())
    result = tool.invoke(
        {
            "cmd": "fake:buffered",
            "workdir": str(linked),
            "reason": "test link rejection",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
        }
    )
    assert json.loads(result.text)["error"] == "linked_path_not_allowed"


def _activation(package: Path) -> ActivatedSkill:
    entry = package / "SKILL.md"
    entry.write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
    record = SkillRecord(
        id="skill_test",
        source="personal",
        name="test",
        description="test",
        entry_path=entry,
        canonical_entry_path=entry,
        package_root=package,
        version="a" * 64,
        frontmatter={},
    )
    return ActivatedSkill(
        record=record, contents=entry.read_text(encoding="utf-8"), version=record.version
    )


def _turn_snapshot(activation: ActivatedSkill) -> SkillTurnSnapshot:
    catalog = SkillCatalogSnapshot(
        generation=1,
        fingerprint="b" * 64,
        entries=(activation.record,),
    )
    return SkillTurnSnapshot(
        catalog=catalog,
        rendered_catalog=RenderedSkillCatalog(
            content="",
            fingerprint=catalog.fingerprint,
            budget_profile="test",
        ),
        activations=(activation,),
    )


def _wait_for_request(broker: ApprovalBroker) -> PendingCommandApproval:
    for _ in range(100):
        requests = broker.pending(kind="command")
        if requests:
            return cast(PendingCommandApproval, requests[0])
        threading.Event().wait(0.01)
    raise AssertionError("approval request was not published")
