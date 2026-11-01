"""Synchronously expose managed runtime files to Codex's sandbox identities."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path

from .models import ManagedEnvironmentError

_CACHE_SECONDS = 300.0
_CACHE_LIMIT = 128
_prepared: OrderedDict[str, tuple[tuple[int, int, int, bytes], float]] = OrderedDict()
_prepare_lock = threading.Lock()


def _read_dacl(root: Path) -> bytes:
    """Read the root's self-relative DACL descriptor; never request SACL privileges."""
    from ctypes import wintypes

    get_security = ctypes.WinDLL("advapi32", use_last_error=True).GetFileSecurityW
    get_security.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_security.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get_security(str(root), 4, None, 0, ctypes.byref(needed))  # DACL_SECURITY_INFORMATION
    if ctypes.get_last_error() != 122 or not 0 < needed.value <= 1024 * 1024:
        raise OSError("Cannot size managed environment DACL")
    buffer = ctypes.create_string_buffer(needed.value)
    if not get_security(str(root), 4, buffer, len(buffer), ctypes.byref(needed)):
        raise OSError("Cannot read managed environment DACL")
    return buffer.raw[: needed.value]


def _identity(root: Path) -> tuple[int, int, int, bytes]:
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("managed environment root must be a real directory")
    state = root.stat(follow_symlinks=False)
    return state.st_dev, state.st_ino, state.st_mtime_ns, _read_dacl(root)


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
            with _prepare_lock:
                key = os.path.normcase(os.path.abspath(root))
                before = _identity(root)
                cached = _prepared.get(key)
                if cached and cached[0] == before and time.monotonic() - cached[1] < _CACHE_SECONDS:
                    _prepared.move_to_end(key)
                    continue
                _prepared.pop(key, None)
                subprocess.run(
                    [str(icacls), str(root), "/grant", "CodexSandboxUsers:(OI)(CI)(RX)", "/Q"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=True,
                    timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                after = _identity(root)
                if before[:2] != after[:2]:
                    raise ValueError(
                        "managed environment root replaced during permission preparation"
                    )
                _prepared[key] = (after, time.monotonic())
                while len(_prepared) > _CACHE_LIMIT:
                    _prepared.popitem(last=False)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ManagedEnvironmentError(
                "managed_environment_read_access_failed",
                "产品执行环境的沙箱读取权限准备失败；命令未启动。请检查沙箱设置与执行环境。",
                provider="execution_environment",
            ) from exc
