"""Production transport/coordinator chain; sandbox provisioning is explicitly fake."""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import ApprovalBroker
from agent.command_runtime import CommandApprovalDecision, ExecCommandTool
from agent.command_runtime.models import CommandProcessSnapshot
from agent.command_runtime.runtime import (
    CommandRuntime,
    CommandRuntimeError,
    _ProcessState,
    sandbox_context,
)
from agent.command_runtime.tools import (
    COMMAND_RUN_CONTEXT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
)
from agent.tool_runtime import ToolRunContext

FIXTURE = Path(__file__).parent / "fixtures/fake_exec_server.py"


@pytest.mark.skipif(os.name != "nt", reason="Windows recovery entrypoint")
@pytest.mark.parametrize("through_tool", [False, True], ids=["runtime", "approved-tool"])
@pytest.mark.parametrize(
    "invalidate_identity", [True, False], ids=["identity-invalid", "identity-intact"]
)
def test_late_start_rejection_allows_manual_repair_and_new_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through_tool: bool, invalidate_identity: bool
) -> None:
    ready = True
    repairs: list[str] = []
    dispatched: list[str] = []
    released = threading.Event()
    runtime = CommandRuntime(
        Path(sys.executable),
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )
    client = runtime._ensure_client()
    original_request = client.request
    original_notification = client._notification

    def notify(method: str, params: dict[str, Any]) -> None:
        original_notification(method, params)
        if method == "fixture/rejections-released":
            released.set()

    def request(method: str, params: dict[str, Any], **kwargs: Any) -> Any:
        if method == "process/start":
            dispatched.append(params["argv"][-1])
            if params["argv"][-1] == "fake:delayed-rejection":
                kwargs["timeout_seconds"] = 0.03
        return original_request(method, params, **kwargs)

    def repair(**kwargs: Any) -> dict[str, object]:
        nonlocal ready
        kwargs["check_cancelled"]()
        assert runtime._sandbox_idle(), "provisioning must not bypass the unresolved start"
        repairs.append("repair")
        ready = True
        runtime._setup_error = None
        return runtime.status()

    # Keep the real client/JSON-RPC and production recovery entrypoint. Only readiness
    # and the OS provisioning action are substituted; never initialize real accounts.
    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(client, "_notification", notify)
    monkeypatch.setattr(runtime, "_exec_server_command", None)
    monkeypatch.setattr(
        runtime,
        "_read_windows_sandbox_state",
        lambda: ("ready" if ready else "repair_required", None),
    )
    monkeypatch.setattr(runtime, "_setup_windows_sandbox", repair)
    runtime._recovery._timeout = 0.3
    runtime._recovery._cooldown = 0
    sandbox = sandbox_context(
        workdir=tmp_path, writable=False, additional_roots=(), network_enabled=False
    )
    broker = ApprovalBroker()
    context = ToolRunContext.create("")
    tool = ExecCommandTool(runtime, broker)

    def start(command: str, process_id: str) -> CommandProcessSnapshot:
        if through_tool:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(
                    tool.invoke,
                    {
                        "cmd": command,
                        "workdir": str(tmp_path),
                        "reason": "recovery regression",
                        COMMAND_SESSION_ID_ARG: "s",
                        COMMAND_RUN_ID_ARG: "r",
                        COMMAND_TOOL_CALL_ID_ARG: process_id,
                        COMMAND_SURFACE_ARG: "desktop_chat",
                        COMMAND_RUN_CONTEXT_ARG: context,
                    },
                )
                for _ in range(200):
                    pending = broker.pending(kind="command")
                    if pending:
                        break
                    if future.done():
                        pytest.fail(f"tool ended before approval: {future.result().text}")
                    threading.Event().wait(0.01)
                assert pending
                assert broker.decide(
                    pending[0].request_id,
                    CommandApprovalDecision(allow=True),
                    session_id="s",
                    run_id="r",
                    expected_kind="command",
                )
                result = future.result(3)
            payload = json.loads(result.text)
            if result.is_error:
                assert payload["retryable"] is False
                raise CommandRuntimeError(payload["error"], payload["message"])
            assert payload["exit_code"] == 0
            return runtime.wait(
                payload["process_id"], session_id="s", run_id="r", timeout_seconds=0
            )
        return runtime.start_process(
            process_id=process_id,
            argv=(command,),
            workdir=tmp_path,
            env={},
            mode="buffered",
            session_id="s",
            run_id="r",
            tool_call_id=process_id,
            sandbox=sandbox,
        )

    try:
        with pytest.raises(CommandRuntimeError) as failure:
            start("fake:delayed-rejection", "proc_failed")
        assert failure.value.code == "process_start_outcome_unknown"
        ready = not invalidate_identity
        with pytest.raises(CommandRuntimeError) as automatic:
            start("fake:buffered", "proc_not_dispatched")
        assert automatic.value.code == "windows_sandbox_recovery_failed"
        assert repairs == []
        assert dispatched == ["fake:delayed-rejection"]
        # Explicit peer rejection arrives after the caller has already stopped waiting.
        client.notify("fixture/release-rejections", {})
        assert released.wait(2)
        runtime.setup_windows_sandbox()
        assert repairs == ["repair"]
        assert runtime._sandbox_idle()
        result = start("fake:buffered", "proc_new")
        result = runtime.wait(result.process_id, session_id="s", run_id="r", timeout_seconds=2)
        assert result.exit_code == 0
        assert result.stdout_tail == b"buffered output"
        assert dispatched == ["fake:delayed-rejection", "fake:buffered"]
    finally:
        context.close_commands()
        runtime.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows recovery entrypoint")
@pytest.mark.parametrize(
    "outcome",
    [
        "pending",
        "disconnected",
        "late_success",
        "unclassified_error",
        "output",
        "exit",
        "closed",
    ],
)
def test_uncertain_or_contradictory_outcome_blocks_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    runtime = CommandRuntime(
        Path(sys.executable),
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )
    client = runtime._ensure_client()
    original_request = client.request
    repairs: list[str] = []

    def request(method: str, params: dict[str, Any], **kwargs: Any) -> Any:
        kwargs["timeout_seconds"] = 0.02
        return original_request(method, params, **kwargs)

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(runtime, "_setup_windows_sandbox", lambda **_: repairs.append("repair"))
    try:
        with pytest.raises(CommandRuntimeError, match="request_timeout"):
            runtime.start_process(
                process_id="proc_unknown",
                argv=("fake:delayed-rejection",),
                workdir=tmp_path,
                env={},
                mode="buffered",
                session_id="s",
                run_id="r",
                tool_call_id="t",
                sandbox=sandbox_context(
                    workdir=tmp_path, writable=False, additional_roots=(), network_enabled=False
                ),
            )
        state = runtime._processes["proc_unknown"]
        handle = state.start_request
        assert handle is not None
        if outcome == "disconnected":
            client.close()
        elif outcome != "pending":
            if outcome in {"output", "exit", "closed"}:
                runtime._handle_notification(
                    {
                        "output": "process/output",
                        "exit": "process/exited",
                        "closed": "process/closed",
                    }[outcome],
                    {
                        "processId": "proc_unknown",
                        "stream": "stdout",
                        "seq": 1,
                        "chunk": "b2s=",
                        "exitCode": 0,
                    },
                )
            response = (
                {"result": {"sandboxType": "windows"}}
                if outcome == "late_success"
                else {
                    "error": {
                        "code": -32603,
                        "message": (
                            "unknown error"
                            if outcome == "unclassified_error"
                            else "windows sandbox failed: orchestrator_helper_launch_canceled: 1223"
                        ),
                    }
                }
            )
            client._dispatch({"id": handle.request_id, **response})
        monkeypatch.setattr(runtime, "_exec_server_command", None)
        runtime._recovery._timeout = 1
        with pytest.raises(CommandRuntimeError, match="resources are unconfirmed"):
            runtime.setup_windows_sandbox()
        assert repairs == []
        assert not runtime._sandbox_idle()
        assert "proc_unknown" in runtime._processes
    finally:
        runtime.close()


def test_old_connection_cannot_clear_new_connection_or_its_grants(tmp_path: Path) -> None:
    runtime = CommandRuntime(
        Path(sys.executable),
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )
    old = runtime._ensure_client()
    try:
        old.close()
        runtime._client = None
        current = runtime._ensure_client()
        runtime._network_grants.add("proc_current")
        # A delayed callback from the previous reader thread must not erase
        # the newly connected service or its authorization state.
        runtime._handle_notification("connection/lost", {"reason": "old EOF"}, source=old)
        assert runtime._client is current
        assert runtime._network_grants == {"proc_current"}
    finally:
        runtime.close()


def test_output_cache_pruning_preserves_unresolved_resource_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.command_runtime.runtime.MAX_RETAINED_TERMINAL_PROCESSES", 1)
    runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
    )
    try:
        for process_id in ("proc_unknown", "proc_old", "proc_latest"):
            runtime._processes[process_id] = _ProcessState(
                CommandProcessSnapshot(
                    process_id=process_id,
                    session_id="s",
                    run_id="r",
                    tool_call_id=process_id,
                    mode="buffered",
                    status="runtime_lost",
                )
            )
            runtime._lifecycle.begin(
                process_id,
                phase="finished",
                resource_state="unverified" if process_id == "proc_unknown" else "closed",
                reconciliation_required=process_id == "proc_unknown",
            )
        runtime._prune_terminal_processes()
        assert set(runtime._processes) == {"proc_unknown", "proc_latest"}
        assert not runtime._sandbox_idle()
    finally:
        runtime.close()
