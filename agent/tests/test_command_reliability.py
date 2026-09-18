from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent.command_runtime import CommandRuntime, CommandRuntimeError
from agent.command_runtime.diagnostics import MAX_EVENTS, CommandDiagnostics
from agent.command_runtime.exec_server import ExecServerClient, _PendingResponse
from agent.command_runtime.network import valid_network_request
from agent.command_runtime.runtime import managed_network_request, sandbox_context
from agent.command_runtime.windows_state import compatible_state, read_state

FAKE = Path(__file__).parent / "fixtures" / "fake_exec_server.py"


def write_state(root: Path) -> None:
    marker = {
        "version": 5,
        "offline_username": "offline",
        "online_username": "online",
        "proxy_ports": [],
    }
    users = {
        "version": 5,
        **{mode: {"username": mode, "password": "YWJj"} for mode in ("offline", "online")},
    }
    for path, value in [
        (root / ".sandbox/setup_marker.json", marker),
        (root / ".sandbox-secrets/sandbox_users.json", users),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("bad", ["version", "empty_users", "password", "bool_port"])
def test_state_rejects_known_false_ready_samples(tmp_path: Path, bad: str) -> None:
    write_state(tmp_path)
    marker = read_state(tmp_path / ".sandbox/setup_marker.json")
    users = read_state(tmp_path / ".sandbox-secrets/sandbox_users.json")
    assert compatible_state(marker, users)
    if bad == "version":
        marker["version"] = 999999
    elif bad == "empty_users":
        users = {}
    elif bad == "password":
        users["online"] = {"username": "online", "password": "not base64"}
    else:
        marker["proxy_ports"] = [True]
    assert not compatible_state(marker, users)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.skipif(os.name != "nt", reason="Windows self-managed proxy")
def test_real_rpc_callback_does_not_consume_start_response(tmp_path: Path, enabled: bool) -> None:
    runtime = CommandRuntime(
        None,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FAKE)],
    )
    try:
        process = runtime.start_process(
            argv=("fake:network",),
            workdir=tmp_path,
            env={},
            mode="buffered",
            session_id="s",
            run_id="r",
            tool_call_id="t",
            sandbox=sandbox_context(
                workdir=tmp_path, writable=False, additional_roots=(), network_enabled=enabled
            ),
        )
        result = runtime.wait(process.process_id, session_id="s", run_id="r", timeout_seconds=5)
        assert result.stdout_tail == (b"allow" if enabled else b"deny")
        assert not runtime._network_grants
    finally:
        runtime.close()


def test_unknown_rpc_method_keeps_pending_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = ExecServerClient([], env={}, notification=lambda *a: None)
    pending = _PendingResponse()
    client._pending[1] = pending
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(client, "_write", written.append)
    client._dispatch({"id": 1, "method": "dangerous/unknown", "params": {}})
    assert not pending.event.is_set()
    assert written[0]["error"]["code"] == -32601
    client._dispatch({"id": 1, "result": {"ok": True}})
    assert pending.event.is_set()


@pytest.mark.parametrize(
    "callback",
    [
        None,
        {},
        {"host": "localhost", "port": 443, "protocol": "https_connect"},
        {"host": "127.0.0.1", "port": 443, "protocol": "http"},
        {"host": "example.com\nsecret", "port": 443, "protocol": "http"},
        {"host": "example.com", "port": True, "protocol": "http"},
    ],
)
def test_callback_denies_invalid_targets(callback: object) -> None:
    assert not valid_network_request(callback)


@pytest.mark.skipif(os.name != "nt", reason="Windows repair state")
def test_failure_is_retained_until_successful_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agent.command_runtime.runtime.probe_setup_helper", lambda _: {"status": "ready"}
    )
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"fake")
    root = tmp_path / "home"
    write_state(root)
    rt = CommandRuntime(
        exe, codex_home=root, execution_root=tmp_path / "exec", base_env={"LANG": "C"}
    )
    assert rt.windows_sandbox_ready
    rt._record_sandbox_failure(
        "visibility", "ShellExecuteExW failed to launch setup helper: 1223 secret"
    )
    assert not rt.windows_sandbox_ready
    assert "secret" not in json.dumps(rt.diagnostics())
    monkeypatch.setattr(rt, "_perform_windows_sandbox_setup", lambda *a, **k: None)

    def fail() -> None:
        raise CommandRuntimeError("windows_sandbox_probe_failed", "fixture failure")

    monkeypatch.setattr(rt, "_probe_windows_sandbox", fail)
    with pytest.raises(CommandRuntimeError):
        rt.setup_windows_sandbox()
    assert not rt.windows_sandbox_ready
    monkeypatch.setattr(rt, "_probe_windows_sandbox", lambda: None)
    assert rt.setup_windows_sandbox()["windows_sandbox_ready"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows repair probes")
@pytest.mark.parametrize("failure", [None, "visibility", "read_only", "writable"])
def test_repair_requires_both_execution_modes_and_filesystem_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from agent.command_runtime.exec_server import ExecServerError

    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"fake")
    root = tmp_path / "home"
    write_state(root)
    rt = CommandRuntime(
        exe,
        codex_home=root,
        execution_root=tmp_path / "exec",
        base_env={"SystemRoot": "C:/Windows"},
    )
    monkeypatch.setattr(
        "agent.command_runtime.runtime.probe_setup_helper", lambda _: {"status": "ready"}
    )
    monkeypatch.setattr(rt, "_perform_windows_sandbox_setup", lambda *a, **k: None)
    rt._record_sandbox_failure("visibility", "orchestrator_helper_launch_canceled")
    calls: list[str] = []
    directories: list[Path] = []

    def request(method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        assert method == "fs/getMetadata"
        assert any(
            e["access"] == "write"
            for e in params["sandbox"]["permissions"]["file_system"]["entries"]
        )
        calls.append("visibility")
        if failure == "visibility":
            raise ExecServerError("orchestrator_helper_launch_canceled: launch setup helper: 1223")
        return {}

    def start(**kwargs: Any) -> SimpleNamespace:
        entries = kwargs["sandbox"]["permissions"]["file_system"]["entries"]
        from agent.command_runtime.runtime import path_to_file_uri

        cwd_entry = next(
            e
            for e in entries
            if e.get("path", {}).get("path") == path_to_file_uri(kwargs["workdir"])
        )
        mode = "writable" if cwd_entry["access"] == "write" else "read_only"
        calls.append(mode)
        directories.append(kwargs["workdir"])
        return SimpleNamespace(process_id=mode)

    def wait(process_id: str, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            terminal=lambda: True,
            exit_code=1 if failure == process_id else 0,
            stdout_tail=b"agent-friend-sandbox-ready",
        )

    monkeypatch.setattr(rt, "_ensure_client", lambda: SimpleNamespace(request=request))
    monkeypatch.setattr(rt, "start_process", start)
    monkeypatch.setattr(rt, "wait", wait)
    if failure:
        with pytest.raises(CommandRuntimeError):
            rt.setup_windows_sandbox()
        assert not rt.windows_sandbox_ready
        assert not any(
            e["stage"] == "setup" and e["code"] == "ok" for e in rt.diagnostics()["events"]
        )
    else:
        assert rt.setup_windows_sandbox()["windows_sandbox_ready"] is True
        assert calls == ["writable", "visibility", "read_only"]
        assert directories[0] != directories[1]
    assert all(not p.exists() for p in directories)


def test_diagnostics_bound_history_and_never_store_operation_text(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.json"
    journal = CommandDiagnostics(path, "private-runtime", "private-build")
    for _ in range(MAX_EVENTS + 10):
        journal.record("policy", "allowed", operation="secret-command", persist=False)
    journal.flush()
    assert len(journal.snapshot()["events"]) == MAX_EVENTS
    assert "secret" not in path.read_text()
    assert "private" not in path.read_text()
    assert len(CommandDiagnostics(path, "other").snapshot()["events"]) == MAX_EVENTS
    assert "evicted" in journal.snapshot()["gaps"]


@pytest.mark.skipif(os.name != "nt", reason="Windows self-managed proxy")
def test_self_managed_proxy_explicitly_enables_callback() -> None:
    assert managed_network_request(True, {})["networkProxy"]["policyDecisionTimeoutMs"] == 5000
    assert managed_network_request(False, {})["networkProxy"] is None


def test_failure_survives_eviction_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    journal = CommandDiagnostics(path, "first")
    journal.record("visibility", "helper_launch_failed", native_code=1223)
    for _ in range(MAX_EVENTS + 1):
        journal.record("policy", "allowed", persist=False)
    journal.flush()
    recovered = CommandDiagnostics(path, "second")
    assert recovered.needs_repair()
    recovered.record("setup", "ok")
    assert not CommandDiagnostics(path, "third").needs_repair()


def test_stderr_inventory_discards_hosts_and_credentials(tmp_path: Path) -> None:
    rt = CommandRuntime(None, codex_home=tmp_path / "home", execution_root=tmp_path / "exec")
    rt._observe_exec_stderr(
        'CODEX_NETWORK_POLICY_VIOLATION {"reason":"not_allowed",'
        '"host":"private.example", "token":"secret"}'
    )
    snapshot = rt.diagnostics()
    assert snapshot["events"][-1]["code"] == "proxy_domain_denied"
    assert "private.example" not in json.dumps(snapshot)
    assert "secret" not in json.dumps(snapshot)


@pytest.mark.skipif(os.name != "nt", reason="Windows proxy lifecycle")
def test_cancel_revokes_network_before_process_termination(tmp_path: Path) -> None:
    rt = CommandRuntime(
        None,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "exec",
        exec_server_command=[sys.executable, str(FAKE)],
    )
    try:
        p = rt.start_process(
            argv=("fake:terminate-stall",),
            workdir=tmp_path,
            env={},
            mode="buffered",
            session_id="s",
            run_id="r",
            tool_call_id="t",
            sandbox=sandbox_context(
                workdir=tmp_path, writable=False, additional_roots=(), network_enabled=True
            ),
        )
        args = {
            "processId": p.process_id,
            "request": {"host": "example.com", "port": 443, "protocol": "https_connect"},
        }
        assert (
            rt._handle_server_request("network/policyRequest", args)["decision"]["type"] == "allow"
        )
        rt.terminate(p.process_id, session_id="s", run_id="r", wait_for_terminal_seconds=0)
        assert (
            rt._handle_server_request("network/policyRequest", args)["decision"]["type"] == "deny"
        )
    finally:
        rt.close()
