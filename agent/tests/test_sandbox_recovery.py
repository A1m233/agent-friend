from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent.command_runtime.exec_server import ExecServerError
from agent.command_runtime.recovery import RecoveryUnavailable, SandboxRecovery
from agent.command_runtime.runtime import CommandRuntime, CommandRuntimeError, sandbox_context
from agent.command_runtime.tools import _command_runtime_error_result
from agent.tool_runtime.control import ToolInvocationCancelled, ToolInvocationControl


@pytest.mark.parametrize(
    "code, status",
    [
        ("windows_sandbox_recovery_failed", "not_started"),
        ("process_start_outcome_unknown", "unknown"),
    ],
)
def test_terminal_recovery_errors_do_not_instruct_blind_retries(code: str, status: str) -> None:
    result = _command_runtime_error_result(
        CommandRuntimeError(code, "fixture"), fallback_status="failed"
    )
    payload = json.loads(result.text)
    assert payload["process_status"] == status
    assert payload["retryable"] is False
    assert result.meta["no_progress_family"] == code
    assert result.meta["requires_user_action"] is True
    if status == "unknown":
        assert "executed" not in result.meta


def test_concurrent_callers_share_repair_and_cancel_independently() -> None:
    entered, release = threading.Event(), threading.Event()
    ready = False
    repairs = 0

    def repair(check: Any) -> None:
        nonlocal ready, repairs
        repairs += 1
        entered.set()
        assert release.wait(2)
        check()
        ready = True

    recovery = SandboxRecovery(
        ready=lambda: ready, idle=lambda: True, repair=repair, retryable=lambda _: False
    )
    first, second = ToolInvocationControl(), ToolInvocationControl()

    def invoke(control: ToolInvocationControl) -> int:
        with recovery.scope(control, lambda: None):
            return recovery.run(lambda: 42)

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(invoke, first)
        assert entered.wait(2)
        b = pool.submit(invoke, second)
        # Wait for the second waiter deterministically through the coordinator lock.
        with recovery._condition:
            assert recovery._condition.wait_for(
                lambda: recovery._flight is not None and recovery._flight.waiters == 2, timeout=2
            )
        first.cancel("user_cancelled")
        with pytest.raises(ToolInvocationCancelled):
            a.result(2)
        release.set()
        assert b.result(2) == 42
    assert repairs == 1
    assert first.recovery_wait_seconds == 480


def test_recovery_waits_for_healthy_process_and_does_not_restart_it() -> None:
    healthy = threading.Event()
    repair_started = threading.Event()
    ready = False

    def repair(check: Any) -> None:
        nonlocal ready
        check()
        assert healthy.is_set()
        repair_started.set()
        ready = True

    recovery = SandboxRecovery(
        ready=lambda: ready, idle=healthy.is_set, repair=repair, retryable=lambda _: False
    )
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(recovery.run, lambda: "continued")
        with recovery._condition:
            assert recovery._condition.wait_for(lambda: recovery._flight is not None, timeout=2)
        assert not repair_started.is_set()
        healthy.set()
        with recovery._condition:
            recovery._condition.notify_all()
        assert result.result(2) == "continued"


def test_recurrence_and_nested_operations_do_not_reset_budget() -> None:
    repairs = 0

    def repair(check: Any) -> None:
        nonlocal repairs
        repairs += 1

    recovery = SandboxRecovery(
        ready=lambda: True,
        idle=lambda: True,
        repair=repair,
        retryable=lambda exc: isinstance(exc, ValueError),
    )
    calls = 0

    def fails() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("known pre-execution failure")

    with (
        recovery.scope(ToolInvocationControl(), lambda: None),
        pytest.raises(RecoveryUnavailable, match="budget"),
    ):
        recovery.run(fails)
    assert calls == 2
    assert repairs == 1
    with pytest.raises(RecoveryUnavailable, match="cooling"):
        recovery.run(fails)
    assert repairs == 1


def test_nonretryable_and_cancelled_calls_do_not_repair_or_execute() -> None:
    calls: list[str] = []
    recovery = SandboxRecovery(
        ready=lambda: True,
        idle=lambda: True,
        repair=lambda _: calls.append("repair"),
        retryable=lambda _: False,
    )
    with pytest.raises(PermissionError):
        recovery.run(lambda: (_ for _ in ()).throw(PermissionError("ACL denied")))
    control = ToolInvocationControl()
    control.cancel("cancelled")
    with recovery.scope(control, lambda: None), pytest.raises(ToolInvocationCancelled):
        recovery.run(lambda: calls.append("executed"))
    assert calls == []


def test_revalidation_failure_after_repair_never_runs_business() -> None:
    ready = False
    calls: list[str] = []

    def repair(check: Any) -> None:
        nonlocal ready
        ready = True

    recovery = SandboxRecovery(
        ready=lambda: ready, idle=lambda: True, repair=repair, retryable=lambda _: False
    )

    def validate() -> None:
        raise PermissionError("authorization revoked")

    with recovery.scope(ToolInvocationControl(), validate), pytest.raises(PermissionError):
        recovery.run(lambda: calls.append("business"))
    assert not calls


def test_busy_runtime_times_out_without_provisioning() -> None:
    repairs: list[str] = []
    recovery = SandboxRecovery(
        ready=lambda: False,
        idle=lambda: False,
        repair=lambda _: repairs.append("repair"),
        retryable=lambda _: False,
        timeout=0.02,
    )
    with pytest.raises(RecoveryUnavailable):
        recovery.run(lambda: pytest.fail("must not execute"))
    assert repairs == []


def test_failed_readiness_probe_never_resumes_business() -> None:
    recovery = SandboxRecovery(
        ready=lambda: False,
        idle=lambda: True,
        repair=lambda _: None,
        retryable=lambda _: False,
    )
    with pytest.raises(RecoveryUnavailable):
        recovery.run(lambda: pytest.fail("setup exit alone must not permit execution"))


def test_cancelled_last_waiter_keeps_flight_until_dispatched_repair_returns() -> None:
    entered, release = threading.Event(), threading.Event()
    next_phase: list[str] = []
    control = ToolInvocationControl()

    def repair(check: Any) -> None:
        entered.set()
        assert release.wait(2)
        check()
        next_phase.append("probe")

    recovery = SandboxRecovery(
        ready=lambda: False,
        idle=lambda: True,
        repair=repair,
        retryable=lambda _: False,
    )

    def invoke() -> None:
        with recovery.scope(control, lambda: None):
            recovery.run(lambda: next_phase.append("business"))

    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(invoke)
        assert entered.wait(2)
        control.cancel("cancelled")
        with pytest.raises(ToolInvocationCancelled):
            result.result(2)
        with recovery._condition:
            flight = recovery._flight
            assert flight is not None
        release.set()
        assert flight.done.wait(2)
    assert next_phase == []


@pytest.mark.parametrize("kind", ["rejected", "timeout", "disconnect", "observed"])
def test_runtime_only_retries_confirmed_unstarted_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import os

    if os.name != "nt":
        pytest.skip("Windows recovery integration")
    runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=["fake"],
    )
    repairs: list[str] = []
    starts: list[str] = []
    monkeypatch.setattr(runtime, "_setup_windows_sandbox", lambda **_: repairs.append("repair"))
    # Keep readiness independent of a retained error; the real setup clears it.
    monkeypatch.setattr(type(runtime), "windows_sandbox_ready", property(lambda _: True))

    def request(method: str, params: dict[str, Any], **_: Any) -> dict[str, str]:
        starts.append(params["processId"])
        if len(starts) == 1:
            if kind == "observed":
                runtime._handle_notification(
                    "process/output",
                    {
                        "processId": params["processId"],
                        "stream": "stdout",
                        "chunk": "b2s=",
                        "seq": 1,
                    },
                )
            error = {
                "code": -32603,
                "message": "windows sandbox failed: orchestrator_helper_launch_canceled: 1223",
            }
            raise ExecServerError(
                "exec_server_request_timeout" if kind == "timeout" else "fixture failure",
                rpc_error=error if kind in {"rejected", "observed"} else None,
            )
        return {"sandboxType": "windowsRestrictedToken"}

    monkeypatch.setattr(
        runtime, "_ensure_client", lambda: SimpleNamespace(request=request, stderr_tail="")
    )
    args: dict[str, Any] = dict(
        argv=("fixture",),
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
    if kind == "rejected":
        runtime.start_process(**args)
        assert len(starts) == 2 and repairs == ["repair"]
    else:
        with pytest.raises(CommandRuntimeError) as caught:
            runtime.start_process(**args)
        assert caught.value.code == "process_start_outcome_unknown"
        assert len(starts) == 1 and not repairs
        assert starts[0] in runtime._processes
        assert not runtime._sandbox_idle()
