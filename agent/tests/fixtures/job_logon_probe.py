"""Probe the logon broker without real credentials, accounts, network or UAC."""

from __future__ import annotations

import ctypes
import importlib
import json
import subprocess
import sys
import types
from ctypes import wintypes
from pathlib import Path

package = types.ModuleType("af_native_probe")
package.__path__ = [str(Path(__file__).parents[2] / "src/agent/command_runtime/lifecycle")]
sys.modules[package.__name__] = package
native = importlib.import_module("af_native_probe.process")
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
security = ctypes.WinDLL("advapi32", use_last_error=True)
create = native._api(
    security,
    "CreateProcessWithLogonW",
    [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(native._StartupInfo),
        ctypes.POINTER(native._ProcessInfo),
    ],
    wintypes.BOOL,
)
open_job = native._api(
    kernel, "OpenJobObjectW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE
)
member = native._api(
    kernel,
    "IsProcessInJob",
    [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
    wintypes.BOOL,
)
close = native._api(kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
terminate = native._api(kernel, "TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
wait = native._api(kernel, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
job = open_job(4, False, sys.stdin.readline().strip())
startup = native._StartupInfo()
startup.cb = ctypes.sizeof(startup)
information = native._ProcessInfo()
command = ctypes.create_unicode_buffer(
    subprocess.list2cmdline(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ]
    )
)
try:
    # LOGON_NETCREDENTIALS_ONLY does not validate the deliberately fictitious
    # credential. The child's only work is a bounded local sleep.
    ok = create(
        "AFContainmentProbe",
        ".",
        "not-a-real-credential",
        2,
        sys.executable,
        command,
        0x08000000 | 0x400,
        None,
        str(Path(sys.executable).parent),
        ctypes.byref(startup),
        ctypes.byref(information),
    )
    if not ok:
        print(json.dumps({"created": False, "native_code": ctypes.get_last_error()}), flush=True)
    else:
        included = wintypes.BOOL()
        queried = bool(member(information.process, job, ctypes.byref(included)))
        print(
            json.dumps({"created": True, "queried": queried, "member": bool(included.value)}),
            flush=True,
        )
finally:
    if information.process:
        terminate(information.process, 0)
        wait(information.process, 5000)
        close(information.process)
        close(information.thread)
    if job:
        close(job)
