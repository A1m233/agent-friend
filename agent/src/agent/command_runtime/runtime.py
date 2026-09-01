"""Process-scoped command runtime using one product-owned exec-server connection."""

from __future__ import annotations

import base64
import binascii
import copy
import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from .exec_server import ExecServerClient, ExecServerError
from .models import (
    CommandMode,
    CommandProcessSnapshot,
    CommandProcessStatus,
    decode_process_output,
)

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
        self.termination_reason: str | None = None
        self.termination_rpc_status: str | None = None
        self.termination_error: str | None = None


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _decode_powershell_clixml_text(value: str) -> str:
    """Return PowerShell error records while discarding progress-only CLIXML."""
    stripped = value.strip()
    if not stripped.startswith("#< CLIXML"):
        return stripped
    xml_text = stripped.removeprefix("#< CLIXML").strip()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return stripped
    errors: list[str] = []
    for element in root.iter():
        if element.attrib.get("S") != "Error" or not element.text:
            continue
        decoded = re.sub(
            r"_x([0-9A-Fa-f]{4})_",
            lambda match: chr(int(match.group(1), 16)),
            element.text,
        ).strip()
        if decoded:
            errors.append(decoded)
    return "\n".join(errors).strip()


def _windows_setup_process_detail(stdout: bytes, stderr: bytes) -> str:
    raw = stderr or stdout
    if not raw:
        return ""
    return _decode_powershell_clixml_text(decode_process_output(raw))


def _windows_setup_was_cancelled(detail: str) -> bool:
    folded = detail.casefold()
    return any(
        marker in folded
        for marker in (
            "operation was canceled by the user",
            "operation was cancelled by the user",
            "the user canceled the operation",
            "the user cancelled the operation",
            "操作被用户取消",
            "用户取消了操作",
        )
    )


def _env_value(values: Mapping[str, str], *names: str) -> str | None:
    """Read an environment value with Windows' case-insensitive semantics."""
    folded = {key.upper(): value for key, value in values.items()}
    return next((folded[name.upper()] for name in names if name.upper() in folded), None)


def windows_sandbox_setup_command(
    executable: Path,
    codex_home: Path,
    base_env: Mapping[str, str],
    *,
    diagnostic_path: Path,
    proxy_port: int | None = None,
) -> list[str]:
    """Build the elevated Codex provisioning command for the product user."""
    system_root = _env_value(base_env, "SystemRoot", "WINDIR")
    username = _env_value(base_env, "USERNAME", "USER")
    if not system_root or not username:
        raise CommandRuntimeError(
            "windows_sandbox_setup_unavailable",
            "Windows system root or signed-in user identity is unavailable",
        )
    user_domain = _env_value(base_env, "USERDOMAIN")
    real_user = f"{user_domain}\\{username}" if user_domain else username
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    setup_args = ""
    if proxy_port is not None:
        setup_args = (
            " -c default_permissions=agent_friend_network"
            " -c permissions.agent_friend_network.extends=':workspace'"
            " -c permissions.agent_friend_network.network.enabled=true"
            " -c permissions.agent_friend_network.network.mode='full'"
            " -c features.network_proxy.enabled=true"
            " -c features.network_proxy.mode='full'"
            f" -c features.network_proxy.proxy_url={_powershell_literal(f'http://127.0.0.1:{proxy_port}')}"
            " -c features.network_proxy.enable_socks5=false"
        )
    child_script = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        f"$env:CODEX_HOME={_powershell_literal(str(codex_home))};"
        f"$diagnosticPath={_powershell_literal(str(diagnostic_path))};"
        "$utf8=New-Object System.Text.UTF8Encoding($false);"
        '[System.IO.File]::WriteAllText($diagnosticPath,"child_started`n",$utf8);'
        "try{"
        "$ErrorActionPreference='Continue';"
        f"$setupOutput=(& {_powershell_literal(str(executable))}{setup_args} "
        "sandbox setup --elevated "
        f"--user {_powershell_literal(real_user)} "
        f"--codex-home {_powershell_literal(str(codex_home))} 2>&1 | Out-String);"
        "$setupExitCode=$LASTEXITCODE;"
        "[System.IO.File]::AppendAllText($diagnosticPath,$setupOutput,$utf8);"
        "[System.IO.File]::AppendAllText($diagnosticPath,"
        '"`nchild_exit_code=$setupExitCode`n",$utf8);'
        "exit $setupExitCode"
        "}catch{"
        "$detail=($_ | Out-String);"
        "[System.IO.File]::AppendAllText($diagnosticPath,"
        '"`nchild_exception: $detail`n",$utf8);'
        "exit 1}"
    )
    child_encoded = _encoded_powershell(child_script)
    parent_script = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
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
        value = str(resolved)
        folded = value.casefold()
        if folded.startswith("\\\\?\\unc\\"):
            value = "\\\\" + value[8:]
        elif folded.startswith("\\\\?\\") and len(value) >= 6 and value[5] == ":":
            value = value[4:]
        value = value.replace("\\", "/")
        if value.startswith("//"):
            return f"file:{quote(value, safe='/:')}"
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
    context: dict[str, Any] = {
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
    if os.name == "nt":
        # Proxy ports are provisioned only by the explicit setup action. Ordinary
        # commands, including network-restricted ones, preserve that global
        # administrator-approved firewall configuration so process/start can never
        # reinterpret a missing child proxy variable as a request to reprovision.
        context["windowsSandboxProxySettingsMode"] = "preserve"
    return context


def safe_loopback_proxy_environment(base_env: Mapping[str, str]) -> dict[str, str]:
    """Return credential-free loopback proxy variables safe for a sandbox child."""
    result: dict[str, str] = {}
    casefolded = {key.upper(): value for key, value in base_env.items()}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = casefolded.get(key)
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if (
                parsed.scheme not in {"http", "https"}
                or host is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                continue
            is_loopback = host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
            if not is_loopback or parsed.port is None:
                continue
        except (ValueError, UnicodeError):
            continue
        result[key] = value
    return result


def safe_loopback_proxy_port(base_env: Mapping[str, str]) -> int | None:
    """Return one unambiguous host proxy port suitable for Windows provisioning."""
    ports: set[int] = set()
    for value in safe_loopback_proxy_environment(base_env).values():
        port = urlsplit(value).port
        if port is not None:
            ports.add(port)
    if len(ports) != 1:
        return None
    return next(iter(ports))


def managed_network_request(
    network_enabled: bool, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Build exec-server fields that make approved network access usable.

    A managed permission profile describes whether network access is allowed, but
    restricted Windows identities cannot use the host network directly. Codex
    0.147.0 expects the client to request an executor-local proxy as well; the
    exec-server then injects proxy variables and grants the sandbox identity access
    only to that proxy.
    """
    if not network_enabled:
        return {
            "enforceManagedNetwork": False,
            "managedNetwork": None,
            "networkProxy": None,
        }
    proxy_ports: set[int] = set()
    for value in safe_loopback_proxy_environment(env or {}).values():
        port = urlsplit(value).port
        if port is not None:
            proxy_ports.add(port)
    loopback_ports = sorted(proxy_ports)
    if loopback_ports:
        return {
            "enforceManagedNetwork": True,
            "managedNetwork": {
                "loopbackPorts": loopback_ports,
                "allowLocalBinding": False,
            },
            "networkProxy": None,
        }
    if os.name != "nt":
        return {
            "enforceManagedNetwork": False,
            "managedNetwork": None,
            "networkProxy": None,
        }
    return {
        "enforceManagedNetwork": True,
        "managedNetwork": None,
        "networkProxy": {
            "proxy": {
                "enabled": True,
                "enableSocks5": False,
                "enableSocks5Udp": False,
                "allowUpstreamProxy": False,
                "dangerouslyAllowAllUnixSockets": False,
                "mode": "full",
                "domains": None,
                "unixSockets": None,
                "allowLocalBinding": False,
            }
        },
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
        runtime_instance_id: str = "",
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
        self._runtime_instance_id = runtime_instance_id
        self._lock = threading.RLock()
        self._setup_lock = threading.Lock()
        self._client: ExecServerClient | None = None
        self._processes: dict[str, _ProcessState] = {}
        self._listeners: list[Callable[[dict[str, object]], None]] = []
        self._evidence_listeners: list[Callable[[CommandProcessSnapshot], None]] = []
        self._capability_listeners: list[Callable[[dict[str, object]], None]] = []
        self._setup_active = False
        self._setup_error: dict[str, str] | None = None
        self._capability_version = 1
        self._capability_fingerprint: tuple[object, ...] | None = None

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
        return self._read_windows_sandbox_state()[0] == "ready"

    def _read_windows_sandbox_state(self) -> tuple[str, dict[str, str] | None]:
        if os.name != "nt":
            return "unsupported", None
        if not self.available:
            return "unsupported", {
                "error": "command_runtime_unavailable",
                "message": "产品内置的 Codex 命令运行时不可用。",
            }
        if self._exec_server_command is not None:
            return "ready", None
        marker_path = self._codex_home / ".sandbox" / "setup_marker.json"
        users_path = self._codex_home / ".sandbox-secrets" / "sandbox_users.json"
        if not marker_path.exists() and not users_path.exists():
            return "setup_required", None
        if not marker_path.is_file() or not users_path.is_file():
            return "repair_required", {
                "error": "windows_sandbox_state_incomplete",
                "message": "Windows 命令沙箱的初始化文件不完整，需要修复。",
            }
        required_proxy_port = safe_loopback_proxy_port(self._base_env)
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            users = json.loads(users_path.read_text(encoding="utf-8"))
            if not isinstance(marker, dict) or not isinstance(marker.get("version"), int):
                raise ValueError("invalid setup marker")
            if not isinstance(users, dict):
                raise ValueError("invalid sandbox users")
            configured_ports = marker.get("proxy_ports")
            if required_proxy_port is not None and (
                not isinstance(configured_ports, list)
                or required_proxy_port not in configured_ports
            ):
                return "repair_required", {
                    "error": "windows_sandbox_proxy_configuration_changed",
                    "message": "当前受治理代理与命令沙箱初始化配置不一致，需要修复。",
                }
            return "ready", None
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, ValueError):
            return "repair_required", {
                "error": "windows_sandbox_state_unreadable",
                "message": "Windows 命令沙箱状态无法读取，需要修复。",
            }

    def sandbox_capability(self) -> dict[str, object]:
        state, detected_error = self._read_windows_sandbox_state()
        with self._lock:
            if self._setup_active:
                state = "setting_up"
                error = None
            elif state == "ready":
                error = None
            else:
                error = self._setup_error or detected_error
                if self._setup_error is not None and state == "setup_required":
                    state = "repair_required"
            return {
                "state": state,
                "supported": os.name == "nt" and self.available,
                "ready": state == "ready",
                "last_error": copy.deepcopy(error),
                "version": self._capability_version,
            }

    def refresh_sandbox_capability(self) -> dict[str, object]:
        snapshot = self.sandbox_capability()
        error = snapshot.get("last_error")
        fingerprint = (
            snapshot["state"],
            snapshot["supported"],
            json.dumps(error, sort_keys=True, ensure_ascii=False) if error else None,
        )
        listeners: tuple[Callable[[dict[str, object]], None], ...] = ()
        with self._lock:
            if fingerprint != self._capability_fingerprint:
                if self._capability_fingerprint is not None:
                    self._capability_version += 1
                self._capability_fingerprint = fingerprint
                snapshot["version"] = self._capability_version
                listeners = tuple(self._capability_listeners)
        for listener in listeners:
            try:
                listener(copy.deepcopy(snapshot))
            except Exception:
                logger.exception("Command sandbox capability listener failed")
        return snapshot

    def status(self) -> dict[str, object]:
        capability = self.refresh_sandbox_capability()
        return {
            "available": self.available,
            "platform": "windows" if os.name == "nt" else "other",
            "sandbox_capability": capability,
            "windows_sandbox_ready": capability["ready"] if os.name == "nt" else True,
            "requires_windows_sandbox_setup": (
                os.name == "nt" and self.available and not capability["ready"]
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
            with self._lock:
                self._setup_active = True
                self._setup_error = None
            self.refresh_sandbox_capability()
            try:
                self._perform_windows_sandbox_setup(executable, timeout_seconds=timeout_seconds)
            except Exception as exc:
                runtime_error = (
                    exc
                    if isinstance(exc, CommandRuntimeError)
                    else CommandRuntimeError(
                        "windows_sandbox_setup_failed",
                        f"Windows sandbox setup failed: {exc}",
                    )
                )
                with self._lock:
                    self._setup_active = False
                    self._setup_error = {
                        "error": runtime_error.code,
                        "message": str(runtime_error),
                    }
                self.refresh_sandbox_capability()
                raise runtime_error from exc
            with self._lock:
                self._setup_active = False
                self._setup_error = None
            self.refresh_sandbox_capability()
            return self.status()

    def _perform_windows_sandbox_setup(self, executable: Path, *, timeout_seconds: float) -> None:
        self._execution_root.mkdir(parents=True, exist_ok=True)
        diagnostic_path = self._execution_root / f"windows-sandbox-setup-{uuid4().hex}.log"
        try:
            completed = subprocess.run(
                windows_sandbox_setup_command(
                    executable,
                    self._codex_home,
                    self._base_env,
                    diagnostic_path=diagnostic_path,
                    proxy_port=safe_loopback_proxy_port(self._base_env),
                ),
                env=self._windows_setup_env(),
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            detail = self._consume_windows_setup_diagnostic(diagnostic_path)
            suffix = f": {detail[-2000:]}" if detail else ""
            raise CommandRuntimeError(
                "windows_sandbox_setup_timed_out",
                "Windows sandbox setup timed out or its administrator prompt was not accepted"
                f"{suffix}",
            ) from exc
        detail = _windows_setup_process_detail(completed.stdout, completed.stderr)
        child_detail = self._consume_windows_setup_diagnostic(diagnostic_path)
        if child_detail:
            detail = child_detail
        if completed.returncode != 0:
            if _windows_setup_was_cancelled(detail):
                raise CommandRuntimeError(
                    "windows_sandbox_setup_cancelled",
                    "Windows sandbox setup was cancelled before administrator approval completed",
                )
            message = (
                f"Windows sandbox elevated setup process exited with code {completed.returncode}"
            )
            if detail:
                message = f"{message}: {detail[-2000:]}"
            else:
                message = f"{message} without diagnostic output"
            raise CommandRuntimeError("windows_sandbox_setup_failed", message)
        if not self.windows_sandbox_ready:
            required_proxy_port = safe_loopback_proxy_port(self._base_env)
            suffix = (
                f" for required proxy port {required_proxy_port}"
                if required_proxy_port is not None
                else ""
            )
            raise CommandRuntimeError(
                "windows_sandbox_setup_incomplete",
                "Windows sandbox setup exited successfully, but its readiness marker "
                f"was not updated{suffix}",
            )

    @staticmethod
    def _consume_windows_setup_diagnostic(path: Path) -> str:
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        finally:
            with suppress(OSError):
                path.unlink()
        return _decode_powershell_clixml_text(decode_process_output(raw))

    def _windows_setup_env(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
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

    def add_evidence_listener(self, listener: Callable[[CommandProcessSnapshot], None]) -> None:
        """Observe bounded typed snapshots without exposing internal refs to the UI payload."""

        with self._lock:
            self._evidence_listeners.append(listener)

    def add_sandbox_capability_listener(
        self, listener: Callable[[dict[str, object]], None]
    ) -> None:
        with self._lock:
            self._capability_listeners.append(listener)

    def start_process(
        self,
        *,
        process_id: str | None = None,
        argv: tuple[str, ...],
        workdir: Path,
        env: Mapping[str, str],
        mode: CommandMode,
        session_id: str,
        run_id: str,
        tool_call_id: str,
        sandbox: dict[str, Any] | None,
        tool_name: str = "exec_command",
        evidence_ref: str | None = None,
    ) -> CommandProcessSnapshot:
        if sandbox is not None and not self.windows_sandbox_ready:
            raise CommandRuntimeError(
                "windows_sandbox_setup_required",
                "Windows command sandbox is not initialized; run the explicit setup action first",
            )
        client = self._ensure_client()
        process_id = process_id or f"proc_{uuid4().hex}"
        if not process_id.startswith("proc_"):
            raise CommandRuntimeError("invalid_process_id", "process id must use the proc_ prefix")
        with self._lock:
            if process_id in self._processes:
                raise CommandRuntimeError(
                    "duplicate_process_id", "process id is already registered"
                )
        snapshot = CommandProcessSnapshot(
            process_id=process_id,
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            mode=mode,
            tool_name=tool_name,
            evidence_ref=evidence_ref,
            runtime_instance_id=self._runtime_instance_id,
        )
        state = _ProcessState(snapshot)
        with self._lock:
            self._processes[process_id] = state
        permissions = sandbox.get("permissions") if sandbox is not None else None
        network_enabled = isinstance(permissions, dict) and permissions.get("network") == "enabled"
        params = {
            "processId": process_id,
            "argv": list(argv),
            "cwd": path_to_file_uri(workdir),
            "env": dict(env),
            "tty": mode == "streaming",
            "pipeStdin": mode == "streaming",
            "arg0": None,
        }
        if sandbox is not None:
            params["sandbox"] = sandbox
            params.update(managed_network_request(network_enabled, env))
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
        if sandbox is not None and (
            not isinstance(sandbox_type, str) or sandbox_type.lower() == "none"
        ):
            with suppress(ExecServerError):
                client.request("process/terminate", {"processId": process_id}, timeout_seconds=5)
            self._finish(state, "runtime_lost", "sandbox_visibility_unavailable")
            raise CommandRuntimeError(
                "sandbox_visibility_unavailable",
                "exec-server did not confirm an enforced sandbox",
            )
        with state.condition:
            state.snapshot.sandbox_type = sandbox_type if isinstance(sandbox_type, str) else "none"
            state.snapshot.version += 1
        self._publish_state(state)
        return self._copy_snapshot(state.snapshot)

    def prepare_owned_read_only_workdir(
        self,
        workdir: Path,
        sandbox: Mapping[str, Any],
    ) -> None:
        """Synchronously warm a product-owned Windows cwd before a read-only launch."""
        if os.name != "nt":
            return
        target_uri = path_to_file_uri(workdir)
        prepared = copy.deepcopy(dict(sandbox))
        permissions = prepared.get("permissions")
        file_system = permissions.get("file_system") if isinstance(permissions, dict) else None
        entries = file_system.get("entries") if isinstance(file_system, dict) else None
        if not isinstance(entries, list):
            raise CommandRuntimeError(
                "sandbox_visibility_unavailable", "sandbox filesystem entries are unavailable"
            )
        matched = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            locator = entry.get("path")
            if (
                isinstance(locator, dict)
                and locator.get("path") == target_uri
                and entry.get("access") == "read"
            ):
                entry["access"] = "write"
                matched = True
                break
        if not matched:
            return
        try:
            self._ensure_client().request(
                "fs/getMetadata",
                {"path": target_uri, "sandbox": prepared},
                timeout_seconds=60,
            )
        except ExecServerError as exc:
            raise CommandRuntimeError(
                "sandbox_visibility_unavailable",
                f"failed to prepare product-owned read-only workdir {target_uri}: {exc}",
            ) from exc

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
        wait_for_terminal_seconds: float = 5.0,
    ) -> CommandProcessSnapshot:
        if wait_for_terminal_seconds < 0:
            raise CommandRuntimeError(
                "invalid_termination_wait", "termination wait must be non-negative"
            )
        state = self._owned_state(process_id, session_id=session_id, run_id=run_id)
        normalized_reason = reason.strip() or "cancelled"
        should_request = False
        with state.condition:
            if state.snapshot.terminal():
                return self._copy_snapshot(state.snapshot)
            if state.termination_reason is None:
                state.termination_reason = normalized_reason
                state.termination_rpc_status = "requesting"
                state.termination_error = None
                should_request = True

        if should_request:
            client = self._client
            if client is None:
                with state.condition:
                    if not state.snapshot.terminal():
                        state.termination_rpc_status = "failed"
                        state.termination_error = "exec-server is unavailable"
                        state.condition.notify_all()
            else:
                try:
                    response = client.request(
                        "process/terminate", {"processId": process_id}, timeout_seconds=5
                    )
                except ExecServerError as exc:
                    with state.condition:
                        if not state.snapshot.terminal():
                            state.termination_rpc_status = "failed"
                            state.termination_error = str(exc)
                            state.condition.notify_all()
                else:
                    running = response.get("running") if isinstance(response, dict) else None
                    if running is False:
                        # The exec-server no longer owns this process, but the local
                        # adapter missed its terminal notification.  The process cannot
                        # remain managed, yet its exact exit fact is unavailable.
                        self._finish(state, "runtime_lost", "process_state_unavailable")
                    else:
                        # Older pinned exec-server builds returned an empty object.  A
                        # successful RPC response is still an accepted termination
                        # request; process/closed remains the terminal confirmation.
                        with state.condition:
                            if not state.snapshot.terminal():
                                state.termination_rpc_status = "accepted"
                                state.condition.notify_all()

        deadline = time.monotonic() + wait_for_terminal_seconds
        with state.condition:
            while not state.snapshot.terminal():
                if state.termination_rpc_status == "failed":
                    raise CommandRuntimeError(
                        "process_termination_failed",
                        "exec-server could not accept the termination request: "
                        f"{state.termination_error or 'unknown error'}",
                    )
                remaining = deadline - time.monotonic()
                if wait_for_terminal_seconds == 0 or remaining <= 0:
                    break
                state.condition.wait(remaining)
            snapshot = self._copy_snapshot(state.snapshot)

        if wait_for_terminal_seconds > 0 and not snapshot.terminal():
            raise CommandRuntimeError(
                "process_termination_unconfirmed",
                "termination was requested but the managed process has not reached a terminal "
                "state; keep observing this process before starting a replacement",
            )
        return snapshot

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
                self.terminate(
                    process_id,
                    session_id=session_id,
                    run_id=run_id,
                    reason=reason,
                    wait_for_terminal_seconds=0,
                )

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
        canonical_names = {
            "PATH": "PATH",
            "PATHEXT": "PATHEXT",
            "SYSTEMROOT": "SystemRoot",
            "SYSTEMDRIVE": "SystemDrive",
            "WINDIR": "WINDIR",
            "COMSPEC": "ComSpec",
            "LANG": "LANG",
            "LC_ALL": "LC_ALL",
            "TMP": "TMP",
            "TEMP": "TEMP",
            "TMPDIR": "TMPDIR",
        }
        casefolded = {key.upper(): value for key, value in self._base_env.items()}
        env = {
            canonical: casefolded[key]
            for key, canonical in canonical_names.items()
            if key in casefolded
        }
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
            with process_state.condition:
                termination_reason = (
                    process_state.termination_reason
                    if process_state.termination_rpc_status in {"requesting", "accepted"}
                    else None
                )
                sandbox_denied = process_state.snapshot.sandbox_denied
            if termination_reason is not None:
                status: CommandProcessStatus = (
                    "timed_out" if termination_reason == "timeout" else "cancelled"
                )
                self._finish(process_state, status, termination_reason)
            else:
                reason = "sandbox_denied" if sandbox_denied else "exited"
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
        if timer is not None:
            timer.cancel()
        # A terminal waiter may immediately commit the formal tool result and
        # clean up its transient checkpoint. Make the final evidence snapshot
        # durable before exposing the terminal state to that waiter.
        self._publish_evidence_state(state)
        with state.condition:
            state.condition.notify_all()
        self._publish_state(state, include_evidence=False)
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

    def _publish_state(self, state: _ProcessState, *, include_evidence: bool = True) -> None:
        with state.condition:
            state.last_published_monotonic = time.monotonic()
            snapshot = self._copy_snapshot(state.snapshot)
        self._publish(snapshot)
        if include_evidence:
            self._publish_evidence(snapshot)

    def _publish_evidence_state(self, state: _ProcessState) -> None:
        with state.condition:
            snapshot = self._copy_snapshot(state.snapshot)
        self._publish_evidence(snapshot)

    def _publish_evidence(self, snapshot: CommandProcessSnapshot) -> None:
        with self._lock:
            evidence_listeners = tuple(self._evidence_listeners)
        for listener in evidence_listeners:
            try:
                listener(snapshot)
            except Exception:
                logger.warning("command evidence listener failed", exc_info=True)

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
