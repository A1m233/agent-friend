#!/usr/bin/env python3
"""Detect existing agent_bridge processes before dev startup."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass

ALLOW_ENV = "AGENT_FRIEND_DEV_ALLOW_EXISTING_BRIDGE"


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str


def main() -> int:
    if os.environ.get(ALLOW_ENV) == "1":
        return 0

    current_pid = os.getpid()
    matches = [
        proc
        for proc in list_processes()
        if proc.pid != current_pid and is_agent_bridge_command(proc.command)
    ]
    if not matches:
        return 0

    print(
        "  [ERROR] 检测到已有 agent_bridge 进程。为避免 QQ/IM 双 bridge 同时消费同一账号，dev 脚本不会继续启动。",
        file=sys.stderr,
    )
    for proc in matches:
        print(f"          PID={proc.pid} CommandLine={proc.command}", file=sys.stderr)
    print("          请先停止这些进程后重试。确认要绕过时可设置 AGENT_FRIEND_DEV_ALLOW_EXISTING_BRIDGE=1。", file=sys.stderr)
    return 1


def list_processes() -> list[ProcessInfo]:
    if platform.system().lower() == "windows":
        return list_processes_windows()
    return list_processes_posix()


def list_processes_posix() -> list[ProcessInfo]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    processes: list[ProcessInfo] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append(ProcessInfo(pid=pid, command=command.strip()))
    return processes


def list_processes_windows() -> list[ProcessInfo]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -ne $null } | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        ),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except Exception:
        return []
    if not result.stdout.strip():
        return []
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    items = raw if isinstance(raw, list) else [raw]

    processes: list[ProcessInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        command_line = item.get("CommandLine")
        if isinstance(command_line, str):
            processes.append(ProcessInfo(pid=pid, command=command_line))
    return processes


def is_agent_bridge_command(command: str) -> bool:
    normalized = " ".join(command.split())
    return "-m agent_bridge" in normalized


if __name__ == "__main__":
    raise SystemExit(main())
