#!/usr/bin/env python3
"""User-level guard for a single agent-friend dev runtime per machine."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEASE_VERSION = 1
DEFAULT_PORTS = [1420, 1421, 18800, 18900]
LOCK_TIMEOUT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    command: str
    executable: str = ""
    start_id: str = ""


@dataclass(frozen=True)
class PortOwner:
    port: int
    pid: int
    process: ProcessInfo | None
    classification: str


@dataclass
class CommandResult:
    code: int
    lines: list[str]

    def emit(self) -> int:
        stream = sys.stderr if self.code else sys.stdout
        for line in self.lines:
            print(line, file=stream)
        return self.code


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("acquire", "takeover"):
        command = sub.add_parser(name)
        add_context_args(command)
        command.add_argument("--owner-pid", type=int, required=True)

    status = sub.add_parser("status")
    add_context_args(status)

    stop = sub.add_parser("stop")
    add_context_args(stop)

    release = sub.add_parser("release")
    release.add_argument("--owner-pid", type=int, required=True)

    args = parser.parse_args()
    manager = RuntimeManager(RuntimeContext.from_args(args))
    if args.command == "acquire":
        return manager.acquire(takeover=False, owner_pid=args.owner_pid).emit()
    if args.command == "takeover":
        return manager.acquire(takeover=True, owner_pid=args.owner_pid).emit()
    if args.command == "status":
        return manager.status().emit()
    if args.command == "stop":
        return manager.stop().emit()
    if args.command == "release":
        return manager.release(args.owner_pid).emit()
    raise AssertionError(args.command)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def add_context_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--ports", required=True, help="Comma separated required dev ports.")


@dataclass(frozen=True)
class RuntimeContext:
    worktree: Path
    mode: str
    ports: list[int]
    repo_id: str
    branch: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> RuntimeContext:
        worktree = args.worktree.resolve()
        return cls(
            worktree=worktree,
            mode=args.mode,
            ports=parse_ports(args.ports),
            repo_id=git_repo_id(worktree),
            branch=git_branch(worktree),
        )


class RuntimeManager:
    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self.state_dir = runtime_state_dir()
        self.lease_path = self.state_dir / "lease.json"
        self.lock_dir = self.state_dir / "lock"

    def acquire(self, *, takeover: bool, owner_pid: int) -> CommandResult:
        with RuntimeLock(self.lock_dir):
            lines: list[str] = []
            lease = self._load_lease()
            if lease:
                active = self._lease_active(lease)
                if active and not takeover:
                    return CommandResult(1, self._active_owner_lines(lease))
                if active and takeover:
                    lines += self._stop_lease_owner(lease, reason="takeover")
                else:
                    lines.append("  [WARN] dev runtime lease 已失效，已安全回收 stale lease。")
                self._remove_lease()

            blockers = self._port_blockers()
            unknown = [owner for owner in blockers if owner.classification == "unknown"]
            legacy = [owner for owner in blockers if owner.classification != "unknown"]
            if unknown:
                return CommandResult(1, lines + self._unknown_port_lines(unknown))
            if legacy and not takeover:
                return CommandResult(1, lines + self._legacy_runtime_lines(legacy))
            if legacy and takeover:
                lines += self._stop_port_owners(legacy, reason="takeover")

            self._write_lease(owner_pid)
            lines.append(
                "===> dev runtime lease acquired: "
                f"PID={owner_pid} mode={self.context.mode} worktree={self.context.worktree}"
            )
            return CommandResult(0, lines)

    def status(self) -> CommandResult:
        lines: list[str] = []
        with RuntimeLock(self.lock_dir):
            lease = self._load_lease()
            if lease and self._lease_active(lease):
                lines += ["===> active agent-friend dev runtime", *format_lease(lease)]
            elif lease:
                lines += [
                    "===> stale agent-friend dev runtime lease",
                    *format_lease(lease),
                    "     下次启动会自动回收该 stale lease。",
                ]
            else:
                lines.append("===> no active agent-friend dev runtime lease")

            blockers = self._port_blockers()
            if blockers:
                lines.append("===> dev port listeners")
                lines += [format_port_owner(owner) for owner in blockers]
            else:
                lines.append("===> dev ports are free")
        return CommandResult(0, lines)

    def stop(self) -> CommandResult:
        with RuntimeLock(self.lock_dir):
            lines: list[str] = []
            lease = self._load_lease()
            if lease and self._lease_active(lease):
                lines += self._stop_lease_owner(lease, reason="stop")
                self._remove_lease()
            elif lease:
                lines.append("  [WARN] dev runtime lease 已失效，已移除 stale lease。")
                self._remove_lease()

            blockers = self._port_blockers()
            unknown = [owner for owner in blockers if owner.classification == "unknown"]
            legacy = [owner for owner in blockers if owner.classification != "unknown"]
            if legacy:
                lines += self._stop_port_owners(legacy, reason="stop")
            if unknown:
                lines += self._unknown_port_lines(unknown)
                return CommandResult(1, lines)
            if not lines:
                lines.append("===> no agent-friend dev runtime to stop")
            return CommandResult(0, lines)

    def release(self, owner_pid: int) -> CommandResult:
        with RuntimeLock(self.lock_dir):
            lease = self._load_lease()
            if not lease:
                return CommandResult(0, [])
            if int(lease.get("ownerPid", 0)) != owner_pid:
                return CommandResult(0, [])
            expected_start_id = str(lease.get("ownerStartId", ""))
            if expected_start_id and process_start_id(owner_pid) != expected_start_id:
                return CommandResult(0, [])
            self._remove_lease()
        return CommandResult(0, ["===> dev runtime lease released"])

    def _load_lease(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.lease_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _write_lease(self, owner_pid: int) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lease = {
            "version": LEASE_VERSION,
            "ownerPid": owner_pid,
            "ownerStartId": process_start_id(owner_pid),
            "worktree": str(self.context.worktree),
            "gitRepo": self.context.repo_id,
            "branch": self.context.branch,
            "mode": self.context.mode,
            "startedAt": datetime.now(UTC).isoformat(),
            "ports": self.context.ports,
            "platform": platform.platform(),
        }
        tmp = self.lease_path.with_name(f"lease.{owner_pid}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(lease, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.lease_path)

    def _remove_lease(self) -> None:
        self.lease_path.unlink(missing_ok=True)

    def _lease_active(self, lease: dict[str, Any]) -> bool:
        try:
            owner_pid = lease.get("ownerPid")
            if owner_pid is None:
                return False
            pid = int(owner_pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        expected_start_id = str(lease.get("ownerStartId", ""))
        actual_start_id = process_start_id(pid)
        if not actual_start_id:
            return False
        return not expected_start_id or actual_start_id == expected_start_id

    def _port_blockers(self) -> list[PortOwner]:
        processes = {proc.pid: proc for proc in list_processes()}
        owners: list[PortOwner] = []
        for port, pids in port_owners(self.context.ports).items():
            for pid in pids:
                process = processes.get(pid)
                owners.append(
                    PortOwner(
                        port=port,
                        pid=pid,
                        process=process,
                        classification=classify_process(process, self.context),
                    )
                )
        return unique_port_owners(owners)

    def _active_owner_lines(self, lease: dict[str, Any]) -> list[str]:
        return [
            "  [ERROR] 当前用户机器上已有 agent-friend dev runtime，普通启动不会抢占。",
            *format_lease(lease),
            "          处理命令：scripts/dev/run.* status | stop | takeover",
        ]

    def _legacy_runtime_lines(self, owners: list[PortOwner]) -> list[str]:
        return [
            "  [ERROR] 检测到同一仓库的 legacy dev runtime 或其他 worktree 正在占用 dev 资源，普通启动不会自动结束它。",
            *[format_port_owner(owner) for owner in owners],
            "          处理命令：scripts/dev/run.* status | stop | takeover",
        ]

    def _unknown_port_lines(self, owners: list[PortOwner]) -> list[str]:
        return [
            "  [ERROR] dev 端口被未知/非本仓库进程占用，脚本不会自动结束它。",
            *[format_port_owner(owner) for owner in owners],
            "          请手动确认占用来源后再重试。",
        ]

    def _stop_lease_owner(self, lease: dict[str, Any], *, reason: str) -> list[str]:
        pid = int(lease["ownerPid"])
        lines = [f"===> {reason}: stopping active dev runtime PID={pid}"]
        if stop_process_tree(pid):
            lines.append(f"  [OK] stopped PID={pid}")
        else:
            lines.append(f"  [WARN] PID={pid} stop command did not fully confirm exit")
        return lines

    def _stop_port_owners(self, owners: list[PortOwner], *, reason: str) -> list[str]:
        lines: list[str] = []
        stopped: set[int] = set()
        for owner in owners:
            if owner.pid in stopped:
                continue
            stopped.add(owner.pid)
            lines.append(f"===> {reason}: stopping legacy dev process PID={owner.pid}")
            if stop_process_tree(owner.pid):
                lines.append(f"  [OK] stopped PID={owner.pid}")
            else:
                lines.append(f"  [WARN] PID={owner.pid} stop command did not fully confirm exit")
        return lines


class RuntimeLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.token = uuid.uuid4().hex
        self.fd: int | None = None

    def __enter__(self) -> RuntimeLock:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                self.fd = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                self._write_owner()
                return self
            except FileExistsError:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise SystemExit("dev runtime lock busy; please retry.") from None
                time.sleep(0.05)
            except PermissionError:
                if self.lock_path.is_dir() and self._remove_stale_lock():
                    continue
                raise

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        owned = self._owned_by_this_process()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if owned:
            self.lock_path.unlink(missing_ok=True)

    def _write_owner(self) -> None:
        if self.fd is None:
            raise RuntimeError("runtime lock is not open")
        data = {
            "pid": os.getpid(),
            "startId": process_start_id(os.getpid()),
            "createdAt": datetime.now(UTC).isoformat(),
            "token": self.token,
        }
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        os.write(self.fd, payload)

    def _owned_by_this_process(self) -> bool:
        try:
            owner = json.loads(self._read_lock_owner_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        return isinstance(owner, dict) and owner.get("token") == self.token

    def _remove_stale_lock(self) -> bool:
        try:
            owner = json.loads(self._read_lock_owner_text())
            if not isinstance(owner, dict):
                if self._lock_path_is_recent():
                    return False
                pid = 0
                start_id = ""
            else:
                pid_value = owner.get("pid", 0)
                pid = int(pid_value)
                start_id = str(owner.get("startId", ""))
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            if self._lock_path_is_recent():
                return False
            pid = 0
            start_id = ""
        if pid > 0:
            actual = process_start_id(pid)
            is_old = self._lock_owner_is_old(owner)
            if actual and (not start_id or actual == start_id or not is_old):
                return False
            if not actual and not is_old:
                return False
        if self.lock_path.is_dir():
            shutil.rmtree(self.lock_path, ignore_errors=True)
        else:
            self.lock_path.unlink(missing_ok=True)
        return True

    def _read_lock_owner_text(self) -> str:
        if self.lock_path.is_dir():
            return (self.lock_path / "owner.json").read_text(encoding="utf-8")
        return self.lock_path.read_text(encoding="utf-8")

    def _lock_owner_is_old(self, owner: dict[str, Any]) -> bool:
        created_at = owner.get("createdAt")
        if not isinstance(created_at, str):
            return True
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError:
            return True
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (datetime.now(UTC) - created).total_seconds() > LOCK_TIMEOUT_SECONDS

    def _lock_path_is_recent(self) -> bool:
        try:
            stat = self.lock_path.stat()
        except OSError:
            return False
        return time.time() - stat.st_mtime <= LOCK_TIMEOUT_SECONDS


def parse_ports(raw: str) -> list[int]:
    ports: list[int] = []
    for item in raw.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        ports.append(int(stripped))
    return sorted(set(ports))


def runtime_state_dir() -> Path:
    system = platform.system().lower()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
        return Path(base) / "agent-friend" / "dev-runtime"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "agent-friend" / "dev-runtime"
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / "agent-friend" / "dev-runtime"
    return Path.home() / ".local" / "state" / "agent-friend" / "dev-runtime"


def git_repo_id(worktree: Path) -> str:
    remote = run_text(["git", "-C", str(worktree), "config", "--get", "remote.origin.url"])
    if remote:
        return normalize_git_remote(remote)
    top = run_text(["git", "-C", str(worktree), "rev-parse", "--show-toplevel"])
    return f"local:{Path(top).name if top else worktree.name}"


def git_branch(worktree: Path) -> str:
    branch = run_text(["git", "-C", str(worktree), "branch", "--show-current"])
    if branch:
        return branch
    head = run_text(["git", "-C", str(worktree), "rev-parse", "--short", "HEAD"])
    return f"detached:{head}" if head else "unknown"


def normalize_git_remote(remote: str) -> str:
    value = remote.strip().lower().replace("\\", "/")
    if value.endswith(".git"):
        value = value[:-4]
    return value


def run_text(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def process_start_id(pid: int) -> str:
    proc = next((item for item in list_processes() if item.pid == pid), None)
    if proc and proc.start_id:
        return proc.start_id
    return process_start_id_direct(pid)


def process_start_id_direct(pid: int) -> str:
    system = platform.system().lower()
    if system == "windows":
        return windows_process_start_id(pid)
    if system == "linux":
        stat = Path(f"/proc/{pid}/stat")
        try:
            content = stat.read_text(encoding="utf-8")
        except OSError:
            return ""
        parts = content.rsplit(")", maxsplit=1)
        if len(parts) != 2:
            return ""
        fields = parts[1].strip().split()
        return fields[19] if len(fields) > 19 else ""
    return run_text(["ps", "-p", str(pid), "-o", "lstart="])


def windows_process_start_id(pid: int) -> str:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CreationDate"
        ),
    ]
    return run_text(command)


def list_processes() -> list[ProcessInfo]:
    if platform.system().lower() == "windows":
        return list_processes_windows()
    return list_processes_posix()


def list_processes_windows() -> list[ProcessInfo]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine,ExecutablePath,CreationDate | "
            "ConvertTo-Json -Compress"
        ),
    ]
    raw = run_text(command)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    processes: list[ProcessInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            raw_pid = item.get("ProcessId")
            if raw_pid is None:
                continue
            pid = int(raw_pid)
            ppid = int(item.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        processes.append(
            ProcessInfo(
                pid=pid,
                ppid=ppid,
                name=str(item.get("Name") or ""),
                command=str(item.get("CommandLine") or ""),
                executable=str(item.get("ExecutablePath") or ""),
                start_id=str(item.get("CreationDate") or ""),
            )
        )
    return processes


def list_processes_posix() -> list[ProcessInfo]:
    raw = run_text(["ps", "-axo", "pid=,ppid=,comm=,args="])
    if not raw:
        return []
    processes: list[ProcessInfo] = []
    for line in raw.splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        command = parts[3] if len(parts) >= 4 else parts[2]
        processes.append(
            ProcessInfo(
                pid=pid,
                ppid=ppid,
                name=Path(parts[2]).name,
                command=command,
                executable=parts[2],
                start_id=process_start_id_direct(pid),
            )
        )
    return processes


def port_owners(ports: list[int]) -> dict[int, list[int]]:
    if platform.system().lower() == "windows":
        return port_owners_windows(ports)
    return port_owners_posix(ports)


def port_owners_windows(ports: list[int]) -> dict[int, list[int]]:
    owners: dict[int, list[int]] = {port: [] for port in ports}
    for port in ports:
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
                "Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress"
            ),
        ]
        raw = run_text(command)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                raw_pid = item.get("OwningProcess")
                if raw_pid is None:
                    continue
                pid = int(raw_pid)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                owners[port].append(pid)
    return {port: sorted(set(pids)) for port, pids in owners.items()}


def port_owners_posix(ports: list[int]) -> dict[int, list[int]]:
    owners: dict[int, list[int]] = {}
    for port in ports:
        raw = run_text(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        pids: list[int] = []
        for line in raw.splitlines():
            try:
                pids.append(int(line.strip()))
            except ValueError:
                continue
        owners[port] = sorted(set(pids))
    return owners


def classify_process(process: ProcessInfo | None, context: RuntimeContext) -> str:
    if not process:
        return "unknown"
    text = comparable_text(" ".join([process.command, process.executable]))
    worktree_text = comparable_text(str(context.worktree))
    if worktree_text and worktree_text in text:
        return "current-worktree"
    if looks_like_agent_friend_runtime(text, context):
        return "same-repo-legacy"
    return "unknown"


def looks_like_agent_friend_runtime(text: str, context: RuntimeContext) -> bool:
    if "agent_bridge" in text or "voice_bridge" in text:
        return True
    repo_name = comparable_text(context.worktree.name)
    if repo_name and repo_name not in text:
        return False
    markers = [
        "vite",
        "pnpm",
        "tauri",
        "cargo",
        "agent_bridge",
        "voice_bridge",
        "com.agentfriend.desktop",
        "app.exe",
        "/app",
        "\\app",
    ]
    return any(marker in text for marker in markers)


def comparable_text(value: str) -> str:
    return value.replace("/", "\\").rstrip("\\").lower()


def unique_port_owners(owners: list[PortOwner]) -> list[PortOwner]:
    seen: set[tuple[int, int]] = set()
    unique: list[PortOwner] = []
    for owner in owners:
        key = (owner.port, owner.pid)
        if key in seen:
            continue
        seen.add(key)
        unique.append(owner)
    return unique


def stop_process_tree(pid: int) -> bool:
    if pid <= 0:
        return True
    if platform.system().lower() == "windows":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 or not process_start_id(pid)
    return stop_process_tree_posix(pid)


def stop_process_tree_posix(pid: int) -> bool:
    processes = {proc.pid: proc for proc in list_processes()}
    targets = descendant_pids(pid, processes)
    targets.append(pid)
    for target in reversed(targets):
        try:
            os.kill(target, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if all(not process_start_id(target) for target in targets):
            return True
        time.sleep(0.1)
    for target in reversed(targets):
        try:
            os.kill(target, getattr(signal, "SIGKILL", signal.SIGTERM))
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
    time.sleep(0.1)
    return all(not process_start_id(target) for target in targets)


def descendant_pids(root_pid: int, processes: dict[int, ProcessInfo]) -> list[int]:
    children: dict[int, list[int]] = {}
    for proc in processes.values():
        children.setdefault(proc.ppid, []).append(proc.pid)
    found: list[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def format_lease(lease: dict[str, Any]) -> list[str]:
    ports = ",".join(str(port) for port in lease.get("ports", []))
    return [
        f"          worktree={lease.get('worktree', '<unknown>')}",
        f"          branch={lease.get('branch', '<unknown>')}",
        f"          repo={lease.get('gitRepo', '<unknown>')}",
        f"          mode={lease.get('mode', '<unknown>')}",
        f"          PID={lease.get('ownerPid', '<unknown>')}",
        f"          startedAt={lease.get('startedAt', '<unknown>')}",
        f"          ports={ports or '<unknown>'}",
    ]


def format_port_owner(owner: PortOwner) -> str:
    process = owner.process
    if process:
        command = process.command or process.executable or "<unknown command>"
        return (
            f"          port={owner.port} PID={owner.pid} "
            f"class={owner.classification} command={command}"
        )
    return f"          port={owner.port} PID={owner.pid} class=unknown command=<unknown>"


if __name__ == "__main__":
    raise SystemExit(main())
