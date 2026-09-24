"""Minimal line-delimited JSON-RPC client for ``codex exec-server --listen stdio``."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_RPC_LINE_BYTES = 64 * 1024 * 1024
MAX_STDERR_TAIL_BYTES = 64 * 1024


class ExecServerError(RuntimeError):
    def __init__(self, message: str, *, rpc_error: object = None) -> None:
        super().__init__(message)
        self.rpc_error = rpc_error


@dataclass
class _PendingResponse:
    event: threading.Event = field(default_factory=threading.Event)
    result: object | None = None
    error: object | None = None


class ExecServerClient:
    def __init__(
        self,
        command: list[str],
        *,
        env: Mapping[str, str],
        notification: Callable[[str, dict[str, Any]], None],
        server_request: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        stderr_observer: Callable[[str], None] | None = None,
    ) -> None:
        self._command = list(command)
        self._env = dict(env)
        self._notification = notification
        self._server_request = server_request
        self._stderr_observer = stderr_observer
        self._process: subprocess.Popen[bytes] | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, _PendingResponse] = {}
        self._next_id = 1
        self._closed = threading.Event()
        self._stderr_tail = bytearray()

    @property
    def stderr_tail(self) -> str:
        with self._state_lock:
            return bytes(self._stderr_tail).decode("utf-8", errors="replace")

    def start(self, *, timeout_seconds: float = 10.0) -> None:
        with self._state_lock:
            if self._process is not None:
                return
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                process = subprocess.Popen(
                    self._command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._env,
                    bufsize=0,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise ExecServerError(f"exec_server_start_failed: {exc}") from exc
            self._process = process
        threading.Thread(target=self._read_stdout, daemon=True, name="exec-server-rpc").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="exec-server-stderr").start()
        self.request("initialize", {"clientName": "agent-friend"}, timeout_seconds=timeout_seconds)
        self.notify("initialized", {})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
    ) -> Any:
        with self._state_lock:
            if self._closed.is_set():
                raise ExecServerError("exec_server_disconnected")
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingResponse()
            self._pending[request_id] = pending
        try:
            self._write({"id": request_id, "method": method, "params": params})
            if not pending.event.wait(timeout_seconds):
                raise ExecServerError(f"exec_server_request_timeout: {method}")
            if pending.error is not None:
                raise ExecServerError(
                    f"exec_server_rpc_error: {method}: {pending.error}",
                    rpc_error=pending.error,
                )
            return pending.result
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def close(self) -> None:
        with self._state_lock:
            process = self._process
            self._process = None
        self._closed.set()
        if process is None:
            return
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._fail_pending("exec_server_closed")

    def _write(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_RPC_LINE_BYTES:
            raise ExecServerError("exec_server_request_too_large")
        with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or self._closed.is_set():
                raise ExecServerError("exec_server_disconnected")
            try:
                process.stdin.write(encoded + b"\n")
                process.stdin.flush()
            except OSError as exc:
                self._mark_disconnected(str(exc))
                raise ExecServerError("exec_server_disconnected") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        reason = "exec_server_eof"
        try:
            while line := process.stdout.readline(MAX_RPC_LINE_BYTES + 1):
                if len(line) > MAX_RPC_LINE_BYTES or not line.endswith(b"\n"):
                    reason = "exec_server_invalid_frame"
                    break
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    reason = "exec_server_invalid_json"
                    break
                if not isinstance(message, dict):
                    reason = "exec_server_invalid_message"
                    break
                self._dispatch(message)
        except OSError as exc:
            reason = f"exec_server_read_failed: {exc}"
        self._mark_disconnected(reason)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        tail = b""
        try:
            while chunk := process.stderr.read(4096):
                with self._state_lock:
                    self._stderr_tail.extend(chunk)
                    overflow = len(self._stderr_tail) - MAX_STDERR_TAIL_BYTES
                    if overflow > 0:
                        del self._stderr_tail[:overflow]
                tail += chunk
                while b"\n" in tail:
                    line, tail = tail.split(b"\n", 1)
                    if self._stderr_observer is not None:
                        self._stderr_observer(line.decode("utf-8", errors="replace"))
                tail = tail[-MAX_STDERR_TAIL_BYTES:]
        except OSError:
            logger.debug("exec-server stderr reader stopped", exc_info=True)

    def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        # The two RPC peers have independent ID spaces. A server request must
        # never complete a pending client request with the same numeric ID.
        if "method" in message and request_id is not None:
            method, params = message.get("method"), message.get("params")
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                return
            try:
                if (
                    method != "network/policyRequest"
                    or not isinstance(params, dict)
                    or self._server_request is None
                ):
                    self._write(
                        {
                            "id": request_id,
                            "error": {
                                "code": -32601,
                                "message": "Unsupported server request",
                            },
                        }
                    )
                    return
                result = self._server_request(method, params)
            except Exception:
                result = {"decision": {"type": "deny", "reason": "policy_unavailable"}}
            self._write({"id": request_id, "result": result})
            return
        if isinstance(request_id, int):
            with self._state_lock:
                pending = self._pending.get(request_id)
            if pending is not None:
                pending.result = message.get("result")
                pending.error = message.get("error")
                pending.event.set()
            return
        method = message.get("method")
        params = message.get("params")
        if isinstance(method, str) and isinstance(params, dict):
            try:
                self._notification(method, params)
            except Exception:
                logger.warning("exec-server notification handler failed", exc_info=True)

    def _mark_disconnected(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._fail_pending(reason)
        try:
            self._notification("connection/lost", {"reason": reason})
        except Exception:
            logger.warning("exec-server disconnect handler failed", exc_info=True)

    def _fail_pending(self, reason: str) -> None:
        with self._state_lock:
            pending_items = tuple(self._pending.values())
        for pending in pending_items:
            pending.error = reason
            pending.event.set()
