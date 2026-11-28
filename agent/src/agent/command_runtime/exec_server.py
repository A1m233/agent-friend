"""Minimal line-delimited JSON-RPC client for ``codex exec-server --listen stdio``."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .lifecycle.process import OwnedWindowsProcess

logger = logging.getLogger(__name__)

MAX_RPC_LINE_BYTES = 64 * 1024 * 1024
MAX_STDERR_TAIL_BYTES = 64 * 1024
MAX_PENDING_REQUESTS = 64


class ExecServerError(RuntimeError):
    def __init__(
        self, message: str, *, rpc_error: object = None, request_sent: bool | None = None
    ) -> None:
        super().__init__(message)
        self.rpc_error = rpc_error
        self.request_sent = request_sent


@dataclass(frozen=True)
class ExecServerResponse:
    result: Any = None
    error: object = None
    from_peer: bool = True


@dataclass
class ExecServerRequest:
    request_id: int
    method: str
    event: threading.Event = field(default_factory=threading.Event)
    response: ExecServerResponse | None = None
    completed: Callable[[], None] | None = None


class ExecServerClient:
    def __init__(
        self,
        command: list[str],
        *,
        env: Mapping[str, str],
        notification: Callable[[str, dict[str, Any]], None],
        server_request: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        stderr_observer: Callable[[str], None] | None = None,
        max_ordinary_requests: int | None = None,
    ) -> None:
        if max_ordinary_requests is not None and not 0 < max_ordinary_requests <= 62:
            raise ValueError("max_ordinary_requests must reserve query/control capacity")
        self._command = list(command)
        self.instance_id = uuid4().hex
        self._env = dict(env)
        self._notification = notification
        self._server_request = server_request
        self._stderr_observer = stderr_observer
        self._process: subprocess.Popen[bytes] | OwnedWindowsProcess | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state_changed = threading.Condition(self._state_lock)
        self._pending: dict[int, ExecServerRequest] = {}
        self._max_ordinary_requests = max_ordinary_requests
        self._next_id = 1
        self._closed = threading.Event()
        self._stderr_tail = bytearray()
        self._readers: list[threading.Thread] = []
        self.resources_closed = False

    @property
    def stderr_tail(self) -> str:
        with self._state_lock:
            return bytes(self._stderr_tail).decode("utf-8", errors="replace")

    @property
    def disconnected(self) -> bool:
        return self._closed.is_set()

    def start(self, *, timeout_seconds: float = 10.0) -> None:
        with self._state_lock:
            if self._process is not None:
                return
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                process: subprocess.Popen[bytes] | OwnedWindowsProcess
                if sys.platform == "win32":
                    process = OwnedWindowsProcess(self._command, env=self._env)
                else:
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
        for target, name in (
            (self._read_stdout, "exec-server-rpc"),
            (self._read_stderr, "exec-server-stderr"),
        ):
            reader = threading.Thread(target=target, daemon=True, name=name)
            self._readers.append(reader)
            reader.start()
        self.request("initialize", {"clientName": "agent-friend"}, timeout_seconds=timeout_seconds)
        self.notify("initialized", {})
        if isinstance(process, OwnedWindowsProcess):
            process.mark_service_ready()

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
        on_submitted: Callable[[ExecServerRequest], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Any:
        deadline = time.monotonic() + timeout_seconds

        def check(sent: bool) -> None:
            if check_cancelled is not None:
                try:
                    check_cancelled()
                except Exception as exc:
                    raise ExecServerError(
                        "exec_server_request_cancelled", request_sent=sent
                    ) from exc

        with self._state_changed:
            check(False)
            while not self._closed.is_set() and not self._can_admit(method):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExecServerError(
                        f"exec_server_admission_timeout: {method}", request_sent=False
                    )
                self._state_changed.wait(min(remaining, 0.05))
                check(False)
            if self._closed.is_set():
                raise ExecServerError("exec_server_disconnected", request_sent=False)
            request_id = self._next_id
            self._next_id += 1
            pending = ExecServerRequest(request_id, method)
            self._pending[request_id] = pending
        try:
            if on_submitted is not None:
                on_submitted(pending)
            check(False)
            self._write({"id": request_id, "method": method, "params": params})
        except BaseException:
            self._complete(
                request_id, ExecServerResponse(error="request_write_failed", from_peer=False)
            )
            raise
        # A caller deadline does not retire remote work or its admission slot.
        # The retained handle is completed by a late reply or transport loss.
        while not pending.event.is_set():
            check(True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExecServerError(f"exec_server_request_timeout: {method}", request_sent=True)
            pending.event.wait(min(remaining, 0.05))
        response = pending.response
        assert response is not None
        if response.error is not None:
            raise ExecServerError(
                f"exec_server_rpc_error: {method}: {response.error}",
                rpc_error=response.error if response.from_peer else None,
            )
        return response.result

    @staticmethod
    def _lane(method: str) -> str:
        if method in {"initialize", "process/terminate"}:
            return "control"
        return "query" if method == "process/read" else "ordinary"

    def _can_admit(self, method: str) -> bool:
        if len(self._pending) >= MAX_PENDING_REQUESTS:
            return False
        if self._max_ordinary_requests is None:
            return True
        lane = self._lane(method)
        limit = self._max_ordinary_requests if lane == "ordinary" else 1
        return sum(self._lane(item.method) == lane for item in self._pending.values()) < limit

    def _complete(self, request_id: int, response: ExecServerResponse) -> None:
        with self._state_changed:
            pending = self._pending.pop(request_id, None)
            if pending is not None:
                pending.response = response
                pending.event.set()
                self._state_changed.notify_all()
        if pending is not None and pending.completed is not None:
            try:
                pending.completed()
            except Exception:
                logger.warning("exec-server completion observer failed", exc_info=True)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def close(self) -> None:
        with self._state_lock:
            process = self._process
        self._closed.set()
        self._fail_pending("exec_server_closed")
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
        if isinstance(process, OwnedWindowsProcess):
            if not process.stop_and_wait(timeout=5):
                raise ExecServerError("exec_server_owned_cleanup_unconfirmed")
            for reader in self._readers:
                if reader is not threading.current_thread():
                    reader.join(timeout=1)
            if any(reader.is_alive() for reader in self._readers):
                raise ExecServerError("exec_server_reader_cleanup_unconfirmed")
            self.resources_closed = True
            process.close()
        self._process = None

    def owned_resource_count(self) -> int | None:
        process = self._process
        return process.active_count() if isinstance(process, OwnedWindowsProcess) else None

    def owned_scope_idle(self) -> bool:
        process = self._process
        return isinstance(process, OwnedWindowsProcess) and process.only_service_processes()

    def _write(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_RPC_LINE_BYTES:
            raise ExecServerError("exec_server_request_too_large", request_sent=False)
        with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or self._closed.is_set():
                raise ExecServerError("exec_server_disconnected", request_sent=False)
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
        if isinstance(request_id, int) and not isinstance(request_id, bool):
            self._complete(
                request_id,
                ExecServerResponse(result=message.get("result"), error=message.get("error")),
            )
            return
        method = message.get("method")
        params = message.get("params")
        if isinstance(method, str) and isinstance(params, dict):
            try:
                self._notification(method, params)
            except Exception:
                logger.warning("exec-server notification handler failed", exc_info=True)

    def _mark_disconnected(self, reason: str) -> None:
        with self._state_changed:
            if self._closed.is_set():
                return
            self._closed.set()
        self._fail_pending(reason)
        try:
            self._notification("connection/lost", {"reason": reason})
        except Exception:
            logger.warning("exec-server disconnect handler failed", exc_info=True)

    def _fail_pending(self, reason: str) -> None:
        with self._state_changed:
            for pending in self._pending.values():
                pending.response = ExecServerResponse(error=reason, from_peer=False)
                pending.event.set()
            self._pending.clear()
            self._state_changed.notify_all()
