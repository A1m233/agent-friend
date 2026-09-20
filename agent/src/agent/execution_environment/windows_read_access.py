"""Synchronously expose managed runtime files to Codex's sandbox identities."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .models import ManagedEnvironmentError


def prepare_read_access(roots: Iterable[Path]) -> None:
    """Grant only RX on host-selected environment roots, after command approval.

    Codex 0.147.0 delegates read ACL propagation to a background helper. A cold
    Python/PowerShell launch can therefore precede access to its DLLs. Use the
    same sandbox group and inheritable RX grant, but await propagation first.
    Do not reset ACLs, grant write, elevate, or recursively follow linked roots.
    """
    if os.name != "nt":
        return
    system_root = os.environ.get("SYSTEMROOT")
    if not system_root:
        raise ManagedEnvironmentError(
            "managed_environment_read_access_failed",
            "Windows 系统目录不可用，无法准备执行环境读取权限。",
            provider="execution_environment",
        )
    icacls = Path(system_root) / "System32" / "icacls.exe"
    for root in dict.fromkeys(roots):
        try:
            if root.is_symlink() or root.is_junction() or not root.is_dir():
                raise ValueError("managed environment root must be a real directory")
            subprocess.run(
                [str(icacls), str(root), "/grant", "CodexSandboxUsers:(OI)(CI)(RX)", "/Q"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ManagedEnvironmentError(
                "managed_environment_read_access_failed",
                "产品执行环境的沙箱读取权限准备失败；命令未启动。请检查沙箱设置与执行环境。",
                provider="execution_environment",
            ) from exc
