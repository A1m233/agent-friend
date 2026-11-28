from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent.command_runtime import CommandRuntime
from agent.command_runtime.runtime import sandbox_context
from agent.tool_runtime.control import ToolInvocationCancelled

FIXTURE = Path(__file__).parent / "fixtures/fake_exec_server.py"


def test_non_windows_read_access_does_not_enter_windows_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = CommandRuntime(None, codex_home=tmp_path / "home", execution_root=tmp_path / "exec")
    try:
        monkeypatch.setattr(
            runtime._recovery, "run", lambda _: pytest.fail("Windows-only recovery")
        )
        with monkeypatch.context() as context:
            context.setattr("agent.command_runtime.runtime.os", SimpleNamespace(name="posix"))
            runtime.prepare_environment_read_access("proc_posix", [], check_cancelled=lambda: None)
        assert not runtime._lifecycle.records()
    finally:
        runtime.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows resource observer")
def test_close_cannot_join_an_observer_before_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = CommandRuntime(None, codex_home=tmp_path / "home", execution_root=tmp_path / "exec")
    entering, release = threading.Event(), threading.Event()
    original = threading.Thread.start

    def start(thread: threading.Thread) -> None:
        if thread.name == "command-resources":
            entering.set()
            assert release.wait(3)
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with ThreadPoolExecutor(2) as pool:
        scheduling = pool.submit(runtime._schedule_resource_observation)
        assert entering.wait(2)
        closing = pool.submit(runtime.close)
        release.set()
        scheduling.result(3)
        closing.result(3)
    assert runtime._resource_observer is not None
    assert not runtime._resource_observer.is_alive()


@pytest.mark.skipif(os.name != "nt", reason="Windows preparation lifecycle")
def test_cancel_during_metadata_keeps_child_guard_until_late_reply(tmp_path: Path) -> None:
    runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )
    pending, released = threading.Event(), threading.Event()
    client = runtime._ensure_client()
    original = client._notification

    def notify(method: str, params: dict[str, Any]) -> None:
        original(method, params)
        if method == "fixture/metadata-pending":
            pending.set()
        if method == "fixture/preparations-released":
            released.set()

    client._notification = notify
    runtime.register_preparing_process(
        process_id="proc_preparing",
        session_id="s",
        run_id="r",
        tool_call_id="t",
        mode="buffered",
        sandboxed=True,
    )
    workdir = tmp_path / "delay-metadata"
    sandbox = sandbox_context(
        workdir=workdir, writable=False, additional_roots=(), network_enabled=False
    )
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                runtime.prepare_owned_read_only_workdir,
                workdir,
                sandbox,
                operation_id="proc_preparing",
                check_cancelled=lambda: runtime.check_process_cancelled("proc_preparing"),
            )
            assert pending.wait(3)
            runtime.request_process_stop(
                "proc_preparing", session_id="s", run_id="r", reason="cancelled"
            )
            with pytest.raises(ToolInvocationCancelled):
                future.result(2)
        runtime.finish_unstarted_process("proc_preparing", cancelled=True)
        parent = runtime._lifecycle.get("proc_preparing")
        assert parent is not None and parent.execution_result == "not_started"
        assert {record.kind for record in runtime._lifecycle.blockers()} == {"prepare", "command"}
        assert runtime.snapshots()[0].status == "stopping"
        client.notify("fixture/release-preparations", {})
        assert released.wait(2)
        runtime._reconcile_sandbox_starts()
        assert not runtime._lifecycle.blockers(maintenance=True)
        assert runtime.snapshots()[0].execution_result == "not_started"
    finally:
        runtime.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows executor domains")
def test_failed_sandbox_executor_retires_without_stopping_host_command(tmp_path: Path) -> None:
    runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FIXTURE)],
    )

    def start(process: str, sandbox: dict[str, Any] | None) -> Any:
        return runtime.start_process(
            process_id=process,
            argv=("fake:streaming",),
            workdir=tmp_path,
            env={},
            mode="streaming",
            session_id="s",
            run_id="r",
            tool_call_id=process,
            sandbox=sandbox,
        )

    try:
        host = start("proc_host", None)
        sandbox = start("proc_sandbox", {"permissions": {"type": "managed"}})
        sandbox_client = runtime._client
        assert sandbox_client is not None and runtime._host_client is not sandbox_client
        sandbox_client._mark_disconnected("fixture_transport_lost")
        runtime._reconcile_sandbox_starts()
        record = runtime._lifecycle.get(sandbox.process_id)
        assert record is not None and record.resource_state == "closed"
        assert record.execution_result == "unknown"
        runtime.write(host.process_id, b"still-alive", session_id="s", run_id="r")
        result = runtime.wait(host.process_id, session_id="s", run_id="r", timeout_seconds=3)
        assert result.exit_code == 0 and result.stdout_tail == b"input:still-alive"
    finally:
        runtime.close()
