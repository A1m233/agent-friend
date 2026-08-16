"""Process-scoped command runtime using one product-owned exec-server connection."""

from __future__ import annotations

import base64
import binascii
import copy
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from .exec_server import ExecServerClient, ExecServerError
from .models import CommandMode, CommandProcessSnapshot, CommandProcessStatus

OUTPUT_TAIL_BYTES = 256 * 1024
MAX_RETAINED_TERMINAL_PROCESSES = 128
OUTPUT_PUBLISH_INTERVAL_SECONDS = 0.2
OUTPUT_PUBLISH_BYTES = 64 * 1024

logger = logging.getLogger(__name__)


class CommandRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ProcessState:
    def __init__(self, snapshot: CommandProcessSnapshot) -> None:
        self.snapshot = snapshot
        self.condition = threading.Condition()
        self.started_monotonic = time.monotonic()
        self.last_published_monotonic = self.started_monotonic
        self.pending_publish_bytes = 0
        self.publish_timer: threading.Timer | None = None


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _decode_process_output(value: bytes) -> str:
    if value.startswith((b"\xff\xfe", b"\xfe\xff")) or value.count(b"\x00") > len(value) // 4:
        return value.decode("utf-16", errors="replace")
    return value.decode("utf-8", errors="replace")


def windows_elevated_setup_command(
    executable: Path, codex_home: Path, base_env: Mapping[str, str]
) -> list[str]:
    """Build an elevated child that sets product CODEX_HOME before Codex setup."""
    system_root = base_env.get("SystemRoot") or base_env.get("WINDIR")
    if not system_root:
        raise CommandRuntimeError(
            "windows_sandbox_setup_unavailable", "Windows system root is unavailable"
        )
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    child_script = (
        "$ErrorActionPreference='Stop';"
        f"$env:CODEX_HOME={_powershell_literal(str(codex_home))};"
        f"& {_powershell_literal(str(executable))} sandbox setup --elevated --current-user;"
        "exit $LASTEXITCODE"
    )
    child_encoded = _encoded_powershell(child_script)
    parent_script = (
        "$ErrorActionPreference='Stop';"
        f"$p=Start-Process -FilePath {_powershell_literal(str(powershell))} "
        "-ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',"
        f"'{child_encoded}') -Verb RunAs -Wait -PassThru -WindowStyle Hidden;"
        "exit $p.ExitCode"
    )
    return [
        str(powershell),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        _encoded_powershell(parent_script),
    ]


def path_to_file_uri(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        value = resolved.as_posix()
        return f"file:///{quote(value, safe='/:')}"
    return f"file://{quote(str(resolved), safe='/')}"


def sandbox_context(
    *,
    workdir: Path,
    writable: bool,
    additional_roots: tuple[tuple[Path, bool], ...],
    network_enabled: bool,
) -> dict[str, Any]:
    entries = [
        {
            "path": {"type": "path", "path": path_to_file_uri(workdir)},
            "access": "write" if writable else "read",
        }
    ]
    for path, root_writable in additional_roots:
        entries.append(
            {
                "path": {"type": "path", "path": path_to_file_uri(path)},
                "access": "write" if root_writable else "read",
            }
        )
    workspace_roots = [
        path_to_file_uri(workdir),
        *[path_to_file_uri(path) for path, _ in additional_roots],
    ]
    return {
        "permissions": {
            "type": "managed",
            "file_system": {"type": "restricted", "entries": entries},
            "network": "enabled" if network_enabled else "restricted",
        },
        "cwd": path_to_file_uri(workdir),
        "workspaceRoots": workspace_roots,
        # The restricted-token backend only supports Codex's legacy full-read
        # filesystem model.  Managed split read/write roots require the elevated
        # Windows sandbox backend, which applies the requested ACL policy through
        # dedicated sandbox identities and still fails closed when unavailable.
        "windowsSandboxLevel": "elevated" if os.name == "nt" else "disabled",
        "windowsSandboxPrivateDesktop": False,
        "useLegacyLandlock": False,
    }


class CommandRuntime:
    def __init__(
        self,
        codex_executable: Path | None,
        *,
        codex_home: Path,
        execution_root: Path,
        base_env: Mapping[str, str] | None = None,
        output_tail_bytes: int = OUTPUT_TAIL_BYTES,
        exec_server_command: list[str] | None = None,
    ) -> None:
        self._codex_executable = codex_executable.resolve() if codex_executable else None
        self._codex_home = codex_home.resolve()
        self._execution_root = execution_root.resolve()
        if (
            self._codex_home == self._execution_root
            or self._codex_home in self._execution_root.parents
            or self._execution_root in self._codex_home.parents
        ):
            raise ValueError("codex_home and execution_root must be separate, non-nested paths")
        self._base_env = dict(base_env or os.environ)
        self._output_tail_bytes = output_tail_bytes
        self._exec_server_command = list(exec_server_command) if exec_server_command else None
        self._lock = threading.RLock()
        self._setup_lock = threading.Lock()
        self._client: ExecServerClient | None = None
        self._processes: dict[str, _ProcessState] = {}
        self._listeners: list[Callable[[dict[str, object]], None]] = []

    @property
    def available(self) -> bool:
        return self._exec_server_command is not None or (
            self._codex_executable is not None and self._codex_executable.is_file()
        )

    @property
    def execution_root(self) -> Path:
        return self._execution_root

    @property
    def windows_sandbox_ready(self) -> bool:
        if os.name != "nt" or self._exec_server_command is not None:
            return True
        return (self._codex_home / ".sandbox" / "setup_marker.json").is_file() and (
            self._codex_home / ".sandbox-secrets" / "sandbox_users.json"
        ).is_file()

    def status(self) -> dict[str, object]:
        return {
            "available": self.available,
            "platform": "windows" if os.name == "nt" else "other",
            "windows_sandbox_ready": self.windows_sandbox_ready,
            "requires_windows_sandbox_setup": (
                os.name == "nt" and self.available and not self.windows_sandbox_ready
            ),
        }

    def setup_windows_sandbox(self, *, timeout_seconds: float = 300.0) -> dict[str, object]:
        """Run Codex's explicit administrator-backed Windows sandbox setup.

        This method is intentionally separate from process startup.  The bridge only
        calls it from a user-initiated setup endpoint, so an ordinary command can
        never surprise the user with a UAC prompt or mutate machine sandbox state.
        """
        if os.name != "nt":
            raise CommandRuntimeError(
                "windows_sandbox_setup_not_applicable",
                "Windows sandbox setup is only available on Windows",
            )
        if self._exec_server_command is not None:
            return self.status()
        executable = self._codex_executable
        if executable is None or not executable.is_file():
            raise CommandRuntimeError(
                "command_runtime_unavailable", "product Codex executable is unavailable"
            )
        with self._setup_lock:
            if self.windows_sandbox_ready:
                return self.status()
            try:
                completed = subprocess.run(
                    windows_elevated_setup_command(executable, self._codex_home, self._base_env),
                    env=self._windows_setup_env(),
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CommandRuntimeError(
                    "windows_sandbox_setup_timed_out",
                    "Windows sandbox setup timed out or its administrator prompt was not accepted",
                ) from exc
            if completed.returncode != 0 or not self.windows_sandbox_ready:
                raw_detail = completed.stderr or completed.stdout
                detail = _decode_process_output(raw_detail).strip()
                message = "Windows sandbox setup did not complete successfully"
                if detail:
                    message = f"{message}: {detail[-2000:]}"
                raise CommandRuntimeError("windows_sandbox_setup_failed", message)
            return self.status()

    def _windows_setup_env(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "PROGRAMW6432",
            "COMMONPROGRAMFILES",
            "COMMONPROGRAMFILES(X86)",
            "USERNAME",
            "USERDOMAIN",
            "PROCESSOR_ARCHITECTURE",
        }
        env = {key: value for key, value in self._base_env.items() if key.upper() in allowed}
        env["CODEX_HOME"] = str(self._codex_home)
        return env

    def add_listener(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def start_process(
        self,
        *,
        argv: tuple[str, ...],
        workdir: Path,
        env: Mapping[str, str],
        mode: CommandMode,
        session_id: str,
        run_id: str,
        tool_call_id: str,
        sandbox: dict[str, Any],
    ) -> CommandProcessSnapshot:
        if not self.windows_sandbox_ready:
            raise CommandRuntimeError(
                "windows_sandbox_setup_required",
                "Windows command sandbox is not initialized; run the explicit setup action first",
            )
        client = self._ensure_client()
        process_id = f"proc_{uuid4().hex}"
        snapshot = CommandProcessSnapshot(
            process_id=process_id,
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            mode=mode,
        )
        state = _ProcessState(snapshot)
        with self._lock:
            self._processes[process_id] = state
        params = {
            "processId": process_id,
            "argv": list(argv),
            "cwd": path_to_file_uri(workdir),
            "env": dict(env),
            "tty": mode == "streaming",
            "pipeStdin": mode == "streaming",
            "arg0": None,
            "sandbox": sandbox,
            "enforceManagedNetwork": False,
        }
        try:
            # The elevated Windows backend may need to establish or refresh its
            # dedicated sandbox identities before the first process starts.
            response = client.request("process/start", params, timeout_seconds=60)
        except ExecServerError as exc:
            with self._lock:
                self._processes.pop(process_id, None)
            stderr = client.stderr_tail.strip()
            detail = f"{exc}; exec-server stderr: {stderr}" if stderr else str(exc)
            raise CommandRuntimeError("process_start_failed", detail) from exc
        sandbox_type = response.get("sandboxType") if isinstance(response, dict) else None
        if not isinstance(sandbox_type, str) or sandbox_type.lower() == "none":
            with suppress(ExecServerError):
                client.request("process/terminate", {"processId": process_id}, timeout_seconds=5)
            self._finish(state, "runtime_lost", "sandbox_unavailable")
            raise CommandRuntimeError(
                "sandbox_unavailable", "exec-server did not confirm an enforced sandbox"
            )
        with state.condition:
            state.snapshot.sandbox_type = sandbox_type
            state.snapshot.version += 1
        self._publish_state(state)
        return self._copy_snapshot(state.snapshot)

    def wait(
        self,
        process_id: str,
        *,
        session_id: str,
        run_id: str,
        timeout_seconds: float,
        wait_for: str = "exit",
        after_seq: int | None = None,
    ) -> CommandProcessSnapshot:
        state = self._owned_state(process_id, session_id=session_id, run_id=run_id)
        deadline = time.monotonic() + timeout_seconds
        with state.condition:
            baseline = state.snapshot.output_seq if after_seq is None else after_seq
            while True:
                if state.snapshot.terminal():
                    return self._copy_snapshot(state.snapshot)
                if wait_for == "output" and state.snapshot.output_seq > baseline:
                    return self._copy_snapshot(state.snapshot)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._copy_snapshot(state.snapshot)
                state.condition.wait(remaining)

    def write(
        self,
        process_id: str,
        chunk: bytes,
        *,
        session_id: str,
        run_id: str,
    ) -> None:
        state = self._owned_state(process_id, session_id=session_id, run_id=run_id)
        if state.snapshot.mode != "streaming" or state.snapshot.terminal():
            raise CommandRuntimeError("process_not_interactive", "process is not interactive")
        client = self._ensure_client()
        try:
            response = client.request(
                "process/write",
                {
                    "processId": process_id,
                    "chunk": base64.b64encode(chunk).decode("ascii"),
                    "writeId": f"write_{uuid4().hex}",
                },
                timeout_seconds=10,
            )
        except ExecServerError as exc:
            raise CommandRuntimeError("process_write_failed", str(exc)) from exc
        status = response.get("status") if isinstance(response, dict) else None
        if status != "accepted":
            raise CommandRuntimeError(
                "process_write_rejected", f"exec-server rejected stdin write: {status or 'unknown'}"
            )

    def terminate(
        self,
        process_id: str,
        *,
        session_id: str,
        run_id: str,
        reason: str = "cancelled",
    ) -> CommandProcessSnapshot:
        state = self._owned_state(process_id, session_id=session_id, run_id=run_id)
        if not state.snapshot.terminal():
            status: CommandProcessStatus = "timed_out" if reason == "timeout" else "cancelled"
            # The product cancellation is authoritative.  Publish it before the RPC so
            # a fast process/exited notification cannot rewrite the terminal reason.
            self._finish(state, status, reason)
            client = self._client
            if client is not None:
                with suppress(ExecServerError):
                    client.request(
                        "process/terminate", {"processId": process_id}, timeout_seconds=5
                    )
        return self._copy_snapshot(state.snapshot)

    def terminate_run(self, session_id: str, run_id: str, *, reason: str = "cancelled") -> None:
        with self._lock:
            ids = [
                item.snapshot.process_id
                for item in self._processes.values()
                if item.snapshot.session_id == session_id
                and item.snapshot.run_id == run_id
                and not item.snapshot.terminal()
            ]
        for process_id in ids:
            with suppress(CommandRuntimeError):
                self.terminate(process_id, session_id=session_id, run_id=run_id, reason=reason)

    def snapshots(
        self, *, session_id: str | None = None, include_terminal: bool = True
    ) -> tuple[CommandProcessSnapshot, ...]:
        with self._lock:
            return tuple(
                self._copy_snapshot(item.snapshot)
                for item in self._processes.values()
                if (session_id is None or item.snapshot.session_id == session_id)
                and (include_terminal or not item.snapshot.terminal())
            )

    def close(self) -> None:
        with self._lock:
            states = tuple(self._processes.values())
            client = self._client
            self._client = None
        for state in states:
            if not state.snapshot.terminal():
                self._finish(state, "runtime_lost", "runtime_closed")
        if client is not None:
            client.close()

    def _ensure_client(self) -> ExecServerClient:
        with self._lock:
            if self._client is not None:
                return self._client
            executable = self._codex_executable
            if self._exec_server_command is None and (
                executable is None or not executable.is_file()
            ):
                raise CommandRuntimeError(
                    "command_runtime_unavailable", "product Codex executable is unavailable"
                )
            assert executable is not None or self._exec_server_command is not None
            self._codex_home.mkdir(parents=True, exist_ok=True)
            env = self._exec_server_env()
            command = self._exec_server_command or [
                str(executable),
                "exec-server",
                "--strict-config",
                "--listen",
                "stdio",
            ]
            client = ExecServerClient(
                command,
                env=env,
                notification=self._handle_notification,
            )
            try:
                client.start()
            except ExecServerError as exc:
                client.close()
                raise CommandRuntimeError("exec_server_start_failed", str(exc)) from exc
            self._client = client
            return client

    def _exec_server_env(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "PATHEXT",
            "SystemRoot",
            "WINDIR",
            "COMSPEC",
            "LANG",
            "LC_ALL",
            "TMP",
            "TEMP",
            "TMPDIR",
        }
        env = {key: value for key, value in self._base_env.items() if key in allowed}
        env["CODEX_HOME"] = str(self._codex_home)
        env.pop("CODEX_API_KEY", None)
        env.pop("CODEX_ACCESS_TOKEN", None)
        return env

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "connection/lost":
            with self._lock:
                states = tuple(self._processes.values())
                self._client = None
            for state in states:
                if not state.snapshot.terminal():
                    self._finish(state, "runtime_lost", str(params.get("reason") or "lost"))
            return
        process_id = params.get("processId")
        if not isinstance(process_id, str):
            return
        with self._lock:
            process_state = self._processes.get(process_id)
        if process_state is None:
            return
        if method == "process/output":
            self._handle_output(process_state, params)
        elif method == "process/exited":
            with process_state.condition:
                code = params.get("exitCode")
                process_state.snapshot.exit_code = code if isinstance(code, int) else None
                process_state.snapshot.sandbox_denied = params.get("sandboxDenied") is True
                process_state.snapshot.version += 1
                process_state.condition.notify_all()
        elif method == "process/closed":
            reason = "sandbox_denied" if process_state.snapshot.sandbox_denied else "exited"
            self._finish(process_state, "exited", reason)

    def _handle_output(self, state: _ProcessState, params: dict[str, Any]) -> None:
        raw = params.get("chunk")
        stream = params.get("stream")
        seq = params.get("seq")
        if not isinstance(raw, str) or stream not in {"stdout", "stderr", "pty"}:
            return
        try:
            chunk = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error):
            return
        with state.condition:
            current = getattr(state.snapshot, f"{stream}_tail")
            combined = current + chunk
            overflow = max(0, len(combined) - self._output_tail_bytes)
            if overflow:
                combined = combined[overflow:]
                state.snapshot.dropped_bytes += overflow
            setattr(state.snapshot, f"{stream}_tail", combined)
            state.snapshot.output_seq = max(
                state.snapshot.output_seq,
                seq if isinstance(seq, int) else state.snapshot.output_seq + 1,
            )
            state.snapshot.version += 1
            state.condition.notify_all()
        self._schedule_publish(state, len(chunk))

    def _finish(self, state: _ProcessState, status: CommandProcessStatus, reason: str) -> None:
        with state.condition:
            if state.snapshot.terminal():
                return
            timer = state.publish_timer
            state.publish_timer = None
            state.pending_publish_bytes = 0
            state.snapshot.status = status
            state.snapshot.terminal_reason = reason
            state.snapshot.finished_at = datetime.now(UTC).isoformat()
            state.snapshot.duration_seconds = max(0.0, time.monotonic() - state.started_monotonic)
            state.snapshot.version += 1
            state.condition.notify_all()
        if timer is not None:
            timer.cancel()
        self._publish_state(state)
        self._prune_terminal_processes()

    def _owned_state(self, process_id: str, *, session_id: str, run_id: str) -> _ProcessState:
        with self._lock:
            state = self._processes.get(process_id)
        if state is None:
            raise CommandRuntimeError("process_not_found", "process handle is unknown or expired")
        if state.snapshot.session_id != session_id or state.snapshot.run_id != run_id:
            raise CommandRuntimeError("process_owner_mismatch", "process belongs to another run")
        return state

    def _publish(self, snapshot: CommandProcessSnapshot) -> None:
        payload = snapshot.to_dict()
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(payload)
            except Exception:
                logger.warning("command runtime listener failed", exc_info=True)

    def _publish_state(self, state: _ProcessState) -> None:
        with state.condition:
            state.last_published_monotonic = time.monotonic()
            snapshot = self._copy_snapshot(state.snapshot)
        self._publish(snapshot)

    def _schedule_publish(self, state: _ProcessState, byte_count: int) -> None:
        publish_now = False
        with state.condition:
            state.pending_publish_bytes += byte_count
            elapsed = time.monotonic() - state.last_published_monotonic
            if state.pending_publish_bytes >= OUTPUT_PUBLISH_BYTES:
                publish_now = True
                timer = state.publish_timer
                state.publish_timer = None
                state.pending_publish_bytes = 0
            elif state.publish_timer is None:
                delay = max(0.0, OUTPUT_PUBLISH_INTERVAL_SECONDS - elapsed)
                timer = threading.Timer(delay, self._flush_scheduled_publish, args=(state,))
                timer.daemon = True
                state.publish_timer = timer
                timer.start()
                timer = None
            else:
                timer = None
        if timer is not None:
            timer.cancel()
        if publish_now:
            self._publish_state(state)

    def _flush_scheduled_publish(self, state: _ProcessState) -> None:
        with state.condition:
            if state.publish_timer is None:
                return
            state.publish_timer = None
            state.pending_publish_bytes = 0
        self._publish_state(state)

    def _prune_terminal_processes(self) -> None:
        with self._lock:
            terminal_ids = [
                process_id
                for process_id, state in self._processes.items()
                if state.snapshot.terminal()
            ]
            for process_id in terminal_ids[:-MAX_RETAINED_TERMINAL_PROCESSES]:
                self._processes.pop(process_id, None)

    @staticmethod
    def _copy_snapshot(snapshot: CommandProcessSnapshot) -> CommandProcessSnapshot:
        return copy.deepcopy(snapshot)
