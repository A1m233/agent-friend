from __future__ import annotations

import base64
import json
import locale
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from agent.approvals import ApprovalBroker
from agent.command_runtime import (
    CommandApprovalDecision,
    CommandApprovalDetails,
    CommandRuntime,
    CommandRuntimeError,
    ExecCommandTool,
    PendingCommandApproval,
)
from agent.command_runtime.models import (
    CommandMode,
    CommandProcessSnapshot,
    decode_process_output,
)
from agent.command_runtime.runtime import (
    _windows_setup_process_detail,
    managed_network_request,
    path_to_file_uri,
    safe_loopback_proxy_environment,
    safe_loopback_proxy_port,
    sandbox_context,
    windows_sandbox_setup_command,
)
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
    _result,
)
from agent.skills.models import (
    ActivatedSkill,
    RenderedSkillCatalog,
    SkillCatalogSnapshot,
    SkillRecord,
    SkillTurnSnapshot,
)
from agent.tool_runtime import ToolRunContext
from agent.tools import ToolRegistry

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


def test_managed_network_request_uses_approved_loopback_proxy() -> None:
    assert managed_network_request(
        True,
        {
            "HTTP_PROXY": "http://127.0.0.1:15236",
            "HTTPS_PROXY": "http://localhost:15236",
        },
    ) == {
        "enforceManagedNetwork": True,
        "managedNetwork": {
            "loopbackPorts": [15236],
            "allowLocalBinding": False,
        },
        "networkProxy": None,
    }


def test_managed_network_request_stays_disabled_without_approval() -> None:
    assert managed_network_request(False) == {
        "enforceManagedNetwork": False,
        "managedNetwork": None,
        "networkProxy": None,
    }


def test_safe_loopback_proxy_environment_rejects_credentials_and_remote_hosts() -> None:
    assert safe_loopback_proxy_environment(
        {
            "http_proxy": "http://127.0.0.1:15236",
            "HTTPS_PROXY": "http://user:secret@localhost:15236",
            "ALL_PROXY": "http://proxy.example:8080",
        }
    ) == {"HTTP_PROXY": "http://127.0.0.1:15236"}


def test_safe_loopback_proxy_port_requires_one_unambiguous_port() -> None:
    assert safe_loopback_proxy_port({"HTTP_PROXY": "http://127.0.0.1:15236"}) == 15236
    assert (
        safe_loopback_proxy_port(
            {
                "HTTP_PROXY": "http://127.0.0.1:15236",
                "HTTPS_PROXY": "http://127.0.0.1:15237",
            }
        )
        is None
    )


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
    assert raised.value.code == "sandbox_visibility_unavailable"


def test_host_user_process_allows_explicit_unsandboxed_start(runtime: CommandRuntime) -> None:
    started = runtime.start_process(
        argv=("shell", "-c", "fake:buffered"),
        workdir=FIXTURE.parent,
        env={},
        mode="buffered",
        session_id="session-a",
        run_id="run-a",
        tool_call_id="call-host",
        sandbox=None,
    )
    finished = runtime.wait(
        started.process_id,
        session_id="session-a",
        run_id="run-a",
        timeout_seconds=2,
    )

    assert started.sandbox_type == "none"
    assert finished.status == "exited"
    assert finished.stdout_tail == b"buffered output"


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


def test_command_output_decoder_preserves_utf8_and_windows_code_page_text() -> None:
    message = "'maa' 不是内部或外部命令"
    assert decode_process_output(message.encode("utf-8"), fallback_encoding="cp936") == message
    assert decode_process_output(message.encode("cp936"), fallback_encoding="cp936") == message


def test_command_output_decoder_ignores_python_utf8_mode_for_legacy_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "找不到指定的模块"
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale=False: "utf-8")
    monkeypatch.setattr(locale, "getencoding", lambda: "cp936")

    assert decode_process_output(message.encode("cp936")) == message


def test_process_snapshot_and_model_result_use_shared_output_decoder() -> None:
    message = "找不到指定的模块"
    raw = message.encode("cp936")
    snapshot = CommandProcessSnapshot(
        process_id="process",
        session_id="session",
        run_id="run",
        tool_call_id="call",
        mode="buffered",
        stdout_tail=raw,
    )

    assert snapshot.to_dict()["stdout"] == message
    assert _bounded_process_result(snapshot)["stdout"] == message


def test_command_error_without_stable_kind_does_not_publish_none_family() -> None:
    result = _result({"status": "exited", "exit_code": 1}, error=True)

    assert result.is_error
    assert "error_kind" not in result.meta
    assert "no_progress_family" not in result.meta


def test_deterministic_command_contract_failure_publishes_no_progress_family() -> None:
    result = _result(
        {"error": "windows_powershell_unsupported", "message": "use pwsh"},
        error=True,
    )

    assert result.is_error
    assert result.meta["no_progress_family"] == "windows_powershell_unsupported"


def test_command_runtime_unavailable_requires_new_runtime_instead_of_retry(
    tmp_path: Path,
) -> None:
    unavailable_runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
    )
    try:
        result = ExecCommandTool(unavailable_runtime, ApprovalBroker()).invoke(
            {COMMAND_SURFACE_ARG: "desktop_chat"}
        )
    finally:
        unavailable_runtime.close()

    payload = json.loads(result.text)
    assert result.is_error is True
    assert result.meta == {
        "error_kind": "command_runtime_unavailable",
        "requires_user_action": True,
        "no_progress_family": "command_runtime_unavailable",
    }
    assert payload["status"] == "unavailable"
    assert payload["retryable"] is False
    assert payload["retry_after"] == "new_runtime_with_command_runtime_available"
    assert payload["required_action"] == "restart_runtime_then_update_or_repair_if_unavailable"
    assert payload["blocking_scope"] == "current_app_runtime"
    assert "重启当前运行实例" in payload["message"]
    assert "更新或修复产品安装" in payload["message"]
    assert "全局系统权限" in payload["message"]


def test_exec_command_description_explains_runtime_unavailable_recovery() -> None:
    assert "command_runtime_unavailable" in ExecCommandTool.description
    assert "停止重试" in ExecCommandTool.description
    assert "更新或修复安装" in ExecCommandTool.description


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
    assert Path(child["AGENT_FRIEND_PERSONAL_SKILLS_DIR"]).is_absolute()
    assert "OPENAI_API_KEY" not in child
    assert "HTTP_PROXY" not in child
    assert "AGENT_FRIEND_INTERNAL_TOKEN" not in child

    network_child = tool._child_env(runtime.execution_root / "temp", network_enabled=True)
    assert "HTTP_PROXY" not in network_child


def test_network_child_inherits_only_safe_loopback_proxy(runtime: CommandRuntime) -> None:
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        base_env={
            "PATH": "safe-path",
            "HTTP_PROXY": "http://127.0.0.1:15236",
            "HTTPS_PROXY": "http://user:secret@localhost:15236",
        },
    )
    child = tool._child_env(runtime.execution_root / "temp", network_enabled=True)
    assert child["HTTP_PROXY"] == "http://127.0.0.1:15236"
    assert "HTTPS_PROXY" not in child


def test_personal_skills_locator_uses_host_injected_root(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    personal = tmp_path / "isolated-personal-skills"
    personal.mkdir()
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        personal_skills_root=personal,
    )
    roots = tool._parse_roots(
        [
            {
                "locator": {"kind": "standard", "name": "personal_skills"},
                "access": "read_write",
            }
        ]
    )
    assert roots == ((personal.resolve(), True),)
    assert tool._child_env(runtime.execution_root / "temp")[
        "AGENT_FRIEND_PERSONAL_SKILLS_DIR"
    ] == str(personal.resolve())


def test_windows_child_environment_normalizes_required_keys_case_insensitively(
    runtime: CommandRuntime,
) -> None:
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        base_env={
            "path": "safe-path",
            "systemroot": "C:\\Windows",
            "systemdrive": "C:",
            "windir": "C:\\Windows",
            "comspec": "C:\\Windows\\System32\\cmd.exe",
        },
    )
    child = tool._child_env(runtime.execution_root / "temp", network_enabled=True)
    assert child["PATH"] == "safe-path"
    assert child["SystemRoot"] == "C:\\Windows"
    assert child["SystemDrive"] == "C:"
    assert child["WINDIR"] == "C:\\Windows"
    assert child["ComSpec"].endswith("cmd.exe")


def test_windows_setup_environment_keeps_os_paths_but_not_credentials(tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        base_env={
            "SystemRoot": "C:\\Windows",
            "SystemDrive": "C:",
            "USERPROFILE": "C:\\Users\\fixture",
            "APPDATA": "C:\\Users\\fixture\\AppData\\Roaming",
            "OPENAI_API_KEY": "secret",
            "HTTP_PROXY": "http://credential@proxy",
            "AGENT_FRIEND_INTERNAL_TOKEN": "secret",
        },
    )
    setup_env = runtime._windows_setup_env()
    assert setup_env["SystemRoot"] == "C:\\Windows"
    assert setup_env["SystemDrive"] == "C:"
    assert setup_env["USERPROFILE"] == "C:\\Users\\fixture"
    assert setup_env["APPDATA"].endswith("Roaming")
    assert setup_env["CODEX_HOME"].endswith("codex-home")
    assert "OPENAI_API_KEY" not in setup_env
    assert "HTTP_PROXY" not in setup_env
    assert "AGENT_FRIEND_INTERNAL_TOKEN" not in setup_env


def test_exec_server_environment_normalizes_windows_keys_case_insensitively(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        base_env={
            "systemroot": "C:\\Windows",
            "systemdrive": "C:",
            "comspec": "C:\\Windows\\System32\\cmd.exe",
        },
    )

    child = runtime._exec_server_env()

    assert child["SystemRoot"] == "C:\\Windows"
    assert child["SystemDrive"] == "C:"
    assert child["ComSpec"].endswith("cmd.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_windows_sandbox_readiness_includes_approved_proxy_port(tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    codex_home = tmp_path / "codex-home"
    marker = codex_home / ".sandbox" / "setup_marker.json"
    users = codex_home / ".sandbox-secrets" / "sandbox_users.json"
    marker.parent.mkdir(parents=True)
    users.parent.mkdir(parents=True)
    users.write_text("{}", encoding="utf-8")
    runtime = CommandRuntime(
        executable,
        codex_home=codex_home,
        execution_root=tmp_path / "execution",
        base_env={"HTTP_PROXY": "http://127.0.0.1:15236"},
    )

    marker.write_text('{"version": 5, "proxy_ports": []}', encoding="utf-8")
    assert not runtime.windows_sandbox_ready
    marker.write_text('{"version": 5, "proxy_ports": [15236]}', encoding="utf-8")
    assert runtime.windows_sandbox_ready


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_windows_sandbox_capability_distinguishes_setup_and_repair(tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    codex_home = tmp_path / "codex-home"
    runtime = CommandRuntime(
        executable,
        codex_home=codex_home,
        execution_root=tmp_path / "execution",
        base_env={},
    )
    capability = runtime.status()["sandbox_capability"]
    assert isinstance(capability, dict)
    assert capability["state"] == "setup_required"

    marker = codex_home / ".sandbox" / "setup_marker.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    capability = runtime.status()["sandbox_capability"]
    assert isinstance(capability, dict)
    assert capability["state"] == "repair_required"


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
def test_command_tool_keeps_approval_pending_until_windows_setup(tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
    )
    broker = ApprovalBroker()
    result_box = []
    args = {
        "cmd": "echo ready",
        "workdir": str(tmp_path.resolve()),
        "reason": "verify first-use setup",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
    }

    thread = threading.Thread(
        target=lambda: result_box.append(ExecCommandTool(runtime, broker).invoke(args))
    )
    thread.start()
    request = _wait_for_request(broker)
    assert request.details.cmd == "echo ready"

    from agent.command_runtime import CommandApprovalDecision

    assert broker.decide(
        request.request_id,
        CommandApprovalDecision(allow=False),
        session_id="session-a",
        run_id="run-a",
        expected_kind="command",
    )
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert json.loads(result_box[0].text)["error"] == "approval_denied"


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_command_tool_validates_arguments_before_windows_setup(tmp_path: Path) -> None:
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
            "cmd": "git clone https://example.test/repo.git",
            "workdir": "%TEMP%",
            "reason": "invalid relative workdir",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
        }
    )
    assert json.loads(result.text)["error"] == "invalid_arguments"
    assert broker.pending(kind="command") == ()


def test_command_additional_roots_accept_standard_locator(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    personal_skills = tmp_path / "skills"
    personal_skills.mkdir()
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        personal_skills_root=personal_skills,
    )
    assert tool._parse_roots(
        [
            {
                "locator": {"kind": "standard", "name": "personal_skills"},
                "access": "read_write",
            }
        ]
    ) == ((personal_skills.resolve(), True),)


def test_command_additional_root_schema_requires_exactly_one_location() -> None:
    root_schema = ExecCommandTool.parameters_schema["properties"]["access"]["properties"][
        "additional_roots"
    ]["items"]

    assert root_schema["required"] == ["access"]
    assert root_schema["oneOf"] == [
        {"required": ["path"]},
        {"required": ["locator"]},
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows PATH scope is Windows-specific")
def test_per_user_path_executable_derives_narrow_scope(
    runtime: CommandRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    local_app_data = profile / "AppData" / "Local"
    executable_root = local_app_data / "Programs" / "maa-cli"
    executable_root.mkdir(parents=True)
    executable = executable_root / "maa.exe"
    executable.write_bytes(b"fixture")
    tool = ExecCommandTool(
        runtime,
        ApprovalBroker(),
        base_env={
            "PATH": str(executable_root),
            "SystemRoot": os.environ["SYSTEMROOT"],
            "USERPROFILE": str(profile),
            "LOCALAPPDATA": str(local_app_data),
        },
    )
    monkeypatch.setattr(shutil, "which", lambda _name, *, path=None: str(executable))

    assert tool._derive_host_executable_roots("maa --version", profile, ()) == (
        (executable_root.resolve(), False),
    )
    assert tool._derive_resolved_executable_roots(executable, profile, ()) == (
        (executable_root.resolve(), False),
    )
    assert tool._derive_host_executable_roots("maa --version", executable_root, ()) == ()
    assert (
        tool._derive_host_executable_roots(
            "maa --version",
            tmp_path,
            ((executable_root, False),),
        )
        == ()
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PATH resolution is Windows-specific")
def test_missing_simple_executable_is_rejected_before_approval(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(
        runtime,
        broker,
        base_env={"PATH": str(tmp_path), "SystemRoot": os.environ["SYSTEMROOT"]},
    )
    result = tool.invoke(
        {
            "cmd": "definitely-missing-agent-friend-tool --version",
            "reason": "check a missing executable",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
        }
    )

    payload = json.loads(result.text)
    assert payload["error"] == "executable_not_found_on_host"
    assert broker.pending(kind="command") == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows PATH scope is Windows-specific")
def test_per_user_path_scope_is_included_in_approval(
    runtime: CommandRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    executable_root = profile / "AppData" / "Local" / "Programs" / "maa-cli"
    executable_root.mkdir(parents=True)
    executable = executable_root / "maa.exe"
    executable.write_bytes(b"fixture")
    broker = ApprovalBroker()
    tool = ExecCommandTool(
        runtime,
        broker,
        base_env={
            "PATH": str(executable_root),
            "SystemRoot": os.environ["SYSTEMROOT"],
            "USERPROFILE": str(profile),
            "LOCALAPPDATA": str(profile / "AppData" / "Local"),
        },
    )
    monkeypatch.setattr(shutil, "which", lambda _name, *, path=None: str(executable))

    args = {
        "cmd": "maa --version",
        "workdir": str(profile),
        "reason": "check the installed version",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
    }
    details, _workdir, roots, _temp, _identities = tool._parse(
        args,
        cast(ToolRunContext, args[COMMAND_RUN_CONTEXT_ARG]),
        session_id="session-a",
        run_id="run-a",
    )
    assert details.host_derived_roots[0].path == str(executable_root.resolve())
    assert (executable_root.resolve(), False) in roots


def test_exec_command_contract_explains_windows_shell_and_host_path_derivation() -> None:
    assert "不能直接执行 Get-Command" in ExecCommandTool.description
    assert "宿主会解析" in ExecCommandTool.description
    assert (
        "可用的 pwsh.exe -NoProfile -Command"
        in ExecCommandTool.parameters_schema["properties"]["cmd"]["description"]
    )
    assert "execution=host_user" in ExecCommandTool.description
    assert "python/python3" in ExecCommandTool.description
    assert "不依赖用户 PATH" in ExecCommandTool.description
    assert "package operation" in ExecCommandTool.description
    assert "argv" in ExecCommandTool.parameters_schema["properties"]
    assert (
        "直接启动单个 CLI" in ExecCommandTool.parameters_schema["properties"]["argv"]["description"]
    )
    assert (
        "普通单程序及参数优先使用 argv"
        in ExecCommandTool.parameters_schema["properties"]["cmd"]["description"]
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows command preflight is Windows-specific")
def test_windows_cmd_rejects_powershell_call_operator_and_unquoted_executable_path() -> None:
    with pytest.raises(CommandRuntimeError) as powershell:
        ExecCommandTool._validate_windows_cmd_shape("& 'C:\\Tools\\adb.exe' version")
    assert powershell.value.code == "powershell_syntax_in_cmd"

    with pytest.raises(CommandRuntimeError) as unquoted:
        ExecCommandTool._validate_windows_cmd_shape(
            "C:\\Program Files\\MuMuPlayer\\MuMuPlayer.exe --launch"
        )
    assert unquoted.value.code == "windows_executable_path_requires_argv"


def test_host_user_requires_structured_argv_and_rejects_fake_access(
    runtime: CommandRuntime,
) -> None:
    tool = ExecCommandTool(runtime, ApprovalBroker())
    context = ToolRunContext.create("")
    common = {
        "reason": "launch a desktop application",
        "execution": "host_user",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
    }

    shell_result = tool.invoke({**common, "cmd": "echo unsafe"})
    assert json.loads(shell_result.text)["error"] == "host_user_requires_argv"

    access_result = tool.invoke(
        {
            **common,
            "argv": [sys.executable, "--version"],
            "access": {"filesystem": "read_only"},
        }
    )
    assert json.loads(access_result.text)["error"] == "host_user_access_is_unrestricted"


def test_host_user_tool_request_discloses_unrestricted_execution(
    runtime: CommandRuntime,
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(runtime, broker)
    result_box = []
    args = {
        "argv": [sys.executable, "fake:buffered"],
        "execution": "host_user",
        "reason": "launch a trusted desktop-user process",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-host",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
    }
    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)))
    thread.start()
    request = _wait_for_request(broker)

    assert request.details.execution == "host_user"
    assert request.details.filesystem_access == "unrestricted"
    assert request.details.network_access == "unrestricted"
    assert request.details.argv[0] == str(Path(sys.executable).resolve())
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
    assert payload["execution"] == "host_user"
    assert payload["sandbox_type"] == "none"
    assert payload["status"] == "exited"


@pytest.mark.skipif(os.name != "nt", reason="Windows status decoding is Windows-specific")
def test_windows_fast_fail_result_is_actionable_without_overclaiming() -> None:
    payload: dict[str, object] = {}
    snapshot = CommandProcessSnapshot(
        process_id="process",
        session_id="session",
        run_id="run",
        tool_call_id="call",
        mode="buffered",
        status="exited",
        exit_code=-1073740791,
        sandbox_type="windowsRestrictedToken",
    )
    details = CommandApprovalDetails(
        cmd="MuMuPlayer.exe",
        argv=("MuMuPlayer.exe",),
        workdir="C:/",
        cwd_source="explicit",
        mode="buffered",
        timeout_seconds=30,
        filesystem_access="read_only",
        additional_roots=(),
        host_derived_roots=(),
        network_access="disabled",
        interactive=False,
    )

    ExecCommandTool._annotate_windows_exit(payload, snapshot, details)

    assert payload["error"] == "windows_process_fast_fail"
    assert payload["windows_status"] == {
        "code": "0xC0000409",
        "name": "STATUS_FAIL_FAST_EXCEPTION",
        "signed_exit_code": -1073740791,
    }
    assert "不能单独证明具体根因" in str(payload["message"])


@pytest.mark.skipif(os.name != "nt", reason="Windows argv normalization is Windows-specific")
def test_explicit_pwsh_command_bypasses_cmd_double_parsing(
    runtime: CommandRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = Path("C:/Program Files/PowerShell/7/pwsh.exe")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *, path=None: str(resolved) if name == "pwsh" else None,
    )
    tool = ExecCommandTool(runtime, ApprovalBroker(), base_env=dict(os.environ))

    argv = tool._shell_argv(
        "pwsh -NoProfile -Command \"Test-Path 'C:\\Program Files\\MuMu\\adb.exe'\"",
        "buffered",
    )

    assert argv == (
        str(resolved.resolve()),
        "-NoProfile",
        "-Command",
        "Test-Path 'C:\\Program Files\\MuMu\\adb.exe'",
    )
    assert "cmd.exe" not in argv[0].casefold()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell rejection is Windows-specific")
def test_windows_powershell_5_is_rejected_before_execution(
    runtime: CommandRuntime,
) -> None:
    tool = ExecCommandTool(runtime, ApprovalBroker(), base_env=dict(os.environ))

    with pytest.raises(CommandRuntimeError, match=r"PowerShell 5\.1") as raised:
        tool._shell_argv(
            'powershell.exe -NoProfile -Command "Get-Process"',
            "buffered",
        )

    assert raised.value.code == "windows_powershell_unsupported"


def test_successful_powershell_echo_of_script_is_rejected_as_false_success() -> None:
    argv = ("powershell.exe", "-NoProfile", "-Command", "Get-Process")

    assert ExecCommandTool._looks_like_unexecuted_powershell(
        argv,
        {"stdout": "Get-Process\r\n"},
    )
    assert not ExecCommandTool._looks_like_unexecuted_powershell(
        argv,
        {"stdout": "Handles  NPM(K)  PM(K)\r\n"},
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows direct executable parsing is Windows-specific")
def test_missing_quoted_absolute_executable_fails_before_sandbox(
    runtime: CommandRuntime,
) -> None:
    tool = ExecCommandTool(runtime, ApprovalBroker(), base_env=dict(os.environ))

    with pytest.raises(CommandRuntimeError) as raised:
        tool._shell_argv('"C:\\definitely-missing\\adb.exe" version', "buffered")

    assert raised.value.code == "host_path_not_found"


def test_windows_setup_command_elevates_for_the_original_product_user(tmp_path: Path) -> None:
    executable = tmp_path / "product codex" / "codex.exe"
    codex_home = tmp_path / "product home"
    command = windows_sandbox_setup_command(
        executable,
        codex_home,
        {
            "SystemRoot": "C:\\Windows",
            "USERNAME": "alice",
            "USERDOMAIN": "EXAMPLE",
        },
        diagnostic_path=tmp_path / "setup diagnostic.log",
        proxy_port=15236,
    )
    parent = base64.b64decode(command[-1]).decode("utf-16-le")
    assert "-Verb RunAs" in parent
    encoded_child = parent.split("'-EncodedCommand','", 1)[1].split("'", 1)[0]
    child = base64.b64decode(encoded_child).decode("utf-16-le")
    assert f"$env:CODEX_HOME='{codex_home}'" in child
    assert f"& '{executable}'" in child
    assert "-c default_permissions=agent_friend_network" in child
    assert "-c permissions.agent_friend_network.extends=':workspace'" in child
    assert "-c permissions.agent_friend_network.network.enabled=true" in child
    assert "-c permissions.agent_friend_network.network.mode='full'" in child
    assert "-c features.network_proxy.enabled=true" in child
    assert "-c features.network_proxy.mode='full'" in child
    assert "-c features.network_proxy.proxy_url='http://127.0.0.1:15236'" in child
    assert "-c features.network_proxy.enable_socks5=false" in child
    assert "sandbox setup --elevated --user 'EXAMPLE\\alice'" in child
    assert f"--codex-home '{codex_home}'" in child
    assert "--current-user" not in child
    assert "exec-server" not in child


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
@pytest.mark.parametrize("network_enabled", [False, True])
def test_windows_sandbox_context_preserves_global_proxy_setup(
    tmp_path: Path, network_enabled: bool
) -> None:
    context = sandbox_context(
        workdir=tmp_path,
        writable=True,
        additional_roots=(),
        network_enabled=network_enabled,
    )

    assert context["windowsSandboxProxySettingsMode"] == "preserve"


@pytest.mark.skipif(os.name != "nt", reason="Windows path URIs are Windows-specific")
@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        (
            r"\\?\C:\Users\Example User\.local\bin",
            "file:///C:/Users/Example%20User/.local/bin",
        ),
        (
            r"\\?\UNC\server\shared tools\bin",
            "file://server/shared%20tools/bin",
        ),
        (
            r"\\server\shared tools\bin",
            "file://server/shared%20tools/bin",
        ),
    ],
)
def test_windows_path_to_file_uri_normalizes_native_and_extended_paths(
    raw_path: str, expected: str
) -> None:
    assert path_to_file_uri(Path(raw_path)) == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox roots are Windows-specific")
def test_windows_sandbox_context_normalizes_extended_additional_root(tmp_path: Path) -> None:
    extended_root = Path(r"\\?\C:\Users\Example User\.local\bin")

    context = sandbox_context(
        workdir=tmp_path,
        writable=True,
        additional_roots=((extended_root, False),),
        network_enabled=False,
    )

    expected = "file:///C:/Users/Example%20User/.local/bin"
    assert context["workspaceRoots"][1] == expected
    assert context["permissions"]["file_system"]["entries"][1] == {
        "path": {"type": "path", "path": expected},
        "access": "read",
    }


def test_windows_setup_process_detail_extracts_errors_and_drops_progress() -> None:
    progress_only = b"""#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><Obj S="progress"><MS><PR N="Record"><AV>Preparing modules for first use.</AV></PR></MS></Obj></Objs>"""
    cancelled = b"""#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><Obj S="progress"><MS><PR N="Record"><AV>Preparing modules for first use.</AV></PR></MS></Obj><S S="Error">Start-Process : The operation was canceled by the user._x000D__x000A_</S></Objs>"""

    assert _windows_setup_process_detail(b"", progress_only) == ""
    assert _windows_setup_process_detail(b"", cancelled) == (
        "Start-Process : The operation was canceled by the user."
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_windows_setup_reports_uac_cancellation_without_clixml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        base_env={"SystemRoot": "C:\\Windows", "USERNAME": "alice"},
    )
    stderr = b"""#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><Obj S="progress"><MS><PR N="Record"><AV>Preparing modules for first use.</AV></PR></MS></Obj><S S="Error">Start-Process : The operation was canceled by the user._x000D__x000A_</S></Objs>"""
    monkeypatch.setattr(
        "agent.command_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", stderr),
    )

    with pytest.raises(CommandRuntimeError) as cancelled:
        runtime.setup_windows_sandbox()

    assert cancelled.value.code == "windows_sandbox_setup_cancelled"
    assert "CLIXML" not in str(cancelled.value)
    assert "cancelled" in str(cancelled.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_windows_setup_surfaces_and_removes_elevated_child_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    execution_root = tmp_path / "execution"
    diagnostic_path = execution_root / "windows-sandbox-setup-fixed.log"
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=execution_root,
        base_env={"SystemRoot": "C:\\Windows", "USERNAME": "alice"},
    )
    fixed_id = type("FixedId", (), {"hex": "fixed"})()
    monkeypatch.setattr("agent.command_runtime.runtime.uuid4", lambda: fixed_id)

    def fail_setup(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_path.write_text(
            "Codex setup could not update the sandbox firewall rule.", encoding="utf-8"
        )
        return subprocess.CompletedProcess(["powershell"], 1, b"", b"")

    monkeypatch.setattr("agent.command_runtime.runtime.subprocess.run", fail_setup)

    with pytest.raises(CommandRuntimeError) as failed:
        runtime.setup_windows_sandbox()

    assert failed.value.code == "windows_sandbox_setup_failed"
    assert "could not update the sandbox firewall rule" in str(failed.value)
    assert not diagnostic_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox setup is Windows-specific")
def test_windows_setup_reports_missing_readiness_marker_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "codex-home",
        execution_root=tmp_path / "execution",
        base_env={
            "SystemRoot": "C:\\Windows",
            "USERNAME": "alice",
            "HTTP_PROXY": "http://127.0.0.1:15236",
        },
    )
    monkeypatch.setattr(
        "agent.command_runtime.runtime.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"", b""),
    )

    with pytest.raises(CommandRuntimeError) as incomplete:
        runtime.setup_windows_sandbox()

    assert incomplete.value.code == "windows_sandbox_setup_incomplete"
    assert "proxy port 15236" in str(incomplete.value)


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
    if os.name == "nt":
        assert payload["host_path_validation"] == {
            "workdir_existed_before_start": True,
            "additional_roots_existed_before_start": True,
            "host_derived_executable_roots": [],
            "sandbox_absence_proves_host_absence": False,
        }
        assert "不能证明宿主机" in payload["sandbox_observation_note"]


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
        "skill": {"skill_id": activation.record.id, "entry": "run.py", "workdir": "."},
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
    assert request.details.skill.workdir == "."
    assert request.details.workdir == "."
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


def test_single_active_skill_binding_is_inferred_without_copying_skill_id(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    package = tmp_path / "skill"
    package.mkdir()
    (package / "run.py").write_text("print('ok')", encoding="utf-8")
    activation = _activation(package)
    broker = ApprovalBroker()
    result_box = []
    args = {
        "cmd": "python run.py",
        "skill": {"entry": "run.py", "workdir": "."},
        "reason": "run the only activated Skill helper",
        COMMAND_SESSION_ID_ARG: "session-a",
        COMMAND_RUN_ID_ARG: "run-a",
        COMMAND_TOOL_CALL_ID_ARG: "call-a",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
        COMMAND_SKILL_SNAPSHOT_ARG: _turn_snapshot(activation),
    }

    thread = threading.Thread(
        target=lambda: result_box.append(ExecCommandTool(runtime, broker).invoke(args))
    )
    thread.start()
    request = _wait_for_request(broker)
    assert request.details.skill is not None
    assert request.details.skill.skill_id == activation.record.id
    assert broker.decide(
        request.request_id,
        CommandApprovalDecision(allow=False),
        session_id="session-a",
        run_id="run-a",
        expected_kind="command",
    )
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_skill_binding_error_distinguishes_filesystem_scope_from_skill_id(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    package = tmp_path / "skill"
    package.mkdir()
    (package / "run.py").write_text("print('ok')", encoding="utf-8")
    activation = _activation(package)
    result = ExecCommandTool(runtime, ApprovalBroker()).invoke(
        {
            "cmd": "python run.py",
            "skill": {
                "skill_id": "scope_0123456789abcdef",
                "entry": "run.py",
                "workdir": ".",
            },
            "reason": "run activated helper",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
            COMMAND_SKILL_SNAPSHOT_ARG: _turn_snapshot(activation),
        }
    )

    payload = json.loads(result.text)
    assert payload["error"] == "skill_not_active"
    assert "filesystem_scope_id" in payload["message"]
    assert activation.record.id in payload["message"]
    assert "call skill_read once" in payload["message"]
    assert "earlier turn's activation" in payload["message"]


def test_skill_binding_without_current_turn_activation_directs_skill_read(
    runtime: CommandRuntime,
) -> None:
    result = ExecCommandTool(runtime, ApprovalBroker()).invoke(
        {
            "cmd": "python run.py",
            "skill": {"entry": "run.py", "workdir": "."},
            "reason": "run helper",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
        }
    )

    payload = json.loads(result.text)
    assert payload["error"] == "skill_id_required"
    assert "call skill_read once" in payload["message"]
    assert "earlier user turn is not valid" in payload["message"]


def test_exec_command_schema_mechanically_separates_workdir_and_skill_binding(
    runtime: CommandRuntime,
) -> None:
    schema = ExecCommandTool.parameters_schema
    assert schema["not"] == {"required": ["workdir", "skill"]}
    skill_schema = schema["properties"]["skill"]
    assert "上一用户轮的激活不会沿用" in skill_schema["description"]
    assert "先调用 skill_read" in skill_schema["description"]
    assert "skill_id" in skill_schema["properties"]
    assert "id" not in skill_schema["properties"]
    assert skill_schema["required"] == ["entry"]

    registry = ToolRegistry([ExecCommandTool(runtime, None)])
    invalid = registry.validate(
        "exec_command",
        {
            "cmd": "echo ok",
            "reason": "invalid mixed cwd",
            "workdir": "C:/explicit",
            "skill": {"entry": "scripts/run.py"},
        },
    )
    assert invalid is not None
    assert invalid.is_error


def test_exec_command_schema_mechanically_restricts_host_user_shape(
    runtime: CommandRuntime,
) -> None:
    registry = ToolRegistry([ExecCommandTool(runtime, None)])

    assert (
        registry.validate(
            "exec_command",
            {
                "argv": ["C:/fixture/MuMuPlayer.exe", "-v", "2"],
                "execution": "host_user",
                "reason": "launch the desktop emulator",
            },
        )
        is None
    )

    for invalid_args in (
        {
            "cmd": '"C:/fixture/MuMuPlayer.exe" -v 2',
            "execution": "host_user",
            "reason": "invalid host shell",
        },
        {
            "argv": ["C:/fixture/MuMuPlayer.exe", "-v", "2"],
            "execution": "host_user",
            "access": {"filesystem": "read_only"},
            "reason": "invalid fake host sandbox",
        },
        {
            "cmd": "python scripts/run.py",
            "execution": "host_user",
            "skill": {"entry": "scripts/run.py"},
            "reason": "invalid host Skill binding",
        },
    ):
        invalid = registry.validate("exec_command", invalid_args)
        assert invalid is not None
        assert invalid.is_error


def test_exec_command_schema_keeps_sandboxed_shell_and_direct_argv_distinct(
    runtime: CommandRuntime,
) -> None:
    registry = ToolRegistry([ExecCommandTool(runtime, None)])

    for valid_args in (
        {
            "cmd": "echo ready && echo done",
            "execution": "sandboxed",
            "reason": "run shell builtins",
        },
        {
            "argv": ["maa", "dir", "log"],
            "execution": "sandboxed",
            "reason": "run one CLI directly",
        },
        {
            "cmd": "python scripts/run.py",
            "skill": {"entry": "scripts/run.py"},
            "reason": "run an activated Skill helper",
        },
    ):
        assert registry.validate("exec_command", valid_args) is None


def test_ordinary_command_without_workdir_uses_stable_session_directory(
    runtime: CommandRuntime, tmp_path: Path
) -> None:
    root = tmp_path / "session-workspaces"
    tool = ExecCommandTool(runtime, ApprovalBroker(), session_workspaces_root=root)

    def parse(session_id: str, run_id: str) -> tuple[CommandApprovalDetails, Path]:
        context = ToolRunContext.create("")
        details, workdir, _roots, _temp, _identities = tool._parse(
            {"cmd": "echo ok", "reason": "verify default cwd"},
            context,
            session_id=session_id,
            run_id=run_id,
        )
        context.close_commands()
        return details, workdir

    first_details, first = parse("im:qq:OPENID-A", "run-a")
    second_details, second = parse("im:qq:OPENID-A", "run-b")
    _other_details, other = parse("another-session", "run-c")

    assert first == second
    assert first != other
    assert first.is_dir()
    assert first_details.cwd_source == "session_managed"
    assert second_details.workdir == str(first)


def test_read_only_session_directory_is_prepared_before_process_start(
    runtime: CommandRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = ApprovalBroker()
    tool = ExecCommandTool(
        runtime,
        broker,
        session_workspaces_root=tmp_path / "session-workspaces",
    )
    prepared: list[Path] = []
    original = runtime.prepare_owned_read_only_workdir

    def prepare(workdir: Path, sandbox: Mapping[str, object]) -> None:
        prepared.append(workdir)
        original(workdir, sandbox)

    monkeypatch.setattr(runtime, "prepare_owned_read_only_workdir", prepare)
    result_box = []
    thread = threading.Thread(
        target=lambda: result_box.append(
            tool.invoke(
                {
                    "cmd": "fake:buffered",
                    "reason": "verify first read-only launch",
                    COMMAND_SESSION_ID_ARG: "session-read-only",
                    COMMAND_RUN_ID_ARG: "run-read-only",
                    COMMAND_TOOL_CALL_ID_ARG: "call-read-only",
                    COMMAND_SURFACE_ARG: "desktop_chat",
                    COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
                }
            )
        )
    )
    thread.start()
    request = _wait_for_request(broker)
    assert broker.decide(
        request.request_id,
        CommandApprovalDecision(allow=True),
        session_id="session-read-only",
        run_id="run-read-only",
        expected_kind="command",
    )
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert prepared == [Path(request.details.workdir)]
    assert json.loads(result_box[0].text)["status"] == "exited"


def test_session_workspace_over_quota_rejects_new_write_request(
    runtime: CommandRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.command_runtime.tools as command_tools

    root = tmp_path / "session-workspaces"
    tool = ExecCommandTool(runtime, ApprovalBroker(), session_workspaces_root=root)
    workdir = tool._session_workdir("session-a")
    (workdir / "existing.bin").write_bytes(b"1234")
    monkeypatch.setattr(command_tools, "MAX_SESSION_WORKSPACE_BYTES", 3)
    context = ToolRunContext.create("")

    with pytest.raises(CommandRuntimeError) as raised:
        tool._parse(
            {
                "cmd": "echo more > next.txt",
                "access": {"filesystem": "read_write"},
                "reason": "write another file",
            },
            context,
            session_id="session-a",
            run_id="run-a",
        )
    context.close_commands()

    assert raised.value.code == "session_workspace_quota_exceeded"


def test_skill_binding_rejects_top_level_workdir(runtime: CommandRuntime, tmp_path: Path) -> None:
    package = tmp_path / "skill"
    package.mkdir()
    (package / "run.py").write_text("print('ok')", encoding="utf-8")
    activation = _activation(package)
    result = ExecCommandTool(runtime, ApprovalBroker()).invoke(
        {
            "cmd": "python run.py",
            "workdir": str(package),
            "skill": {"skill_id": activation.record.id, "entry": "run.py"},
            "reason": "run activated helper",
            COMMAND_SESSION_ID_ARG: "session-a",
            COMMAND_RUN_ID_ARG: "run-a",
            COMMAND_TOOL_CALL_ID_ARG: "call-a",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: ToolRunContext.create(""),
            COMMAND_SKILL_SNAPSHOT_ARG: _turn_snapshot(activation),
        }
    )
    assert result.is_error
    assert "不能同时提供顶层 workdir" in json.loads(result.text)["message"]


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
