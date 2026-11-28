"""Contain only probe-owned workers; this is not a production sandbox supervisor."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("min_ws", ctypes.c_size_t),
        ("max_ws", ctypes.c_size_t),
        ("active", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(f"counter_{i}", ctypes.c_uint64) for i in range(6)]


class _Limits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64),
        ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64),
        ("period_kernel_time", ctypes.c_int64),
        ("page_faults", wintypes.DWORD),
        ("total", wintypes.DWORD),
        ("active", wintypes.DWORD),
        ("terminated", wintypes.DWORD),
    ]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "IsProcessInJob": (
            [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        ),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


def _check(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


class ProbeJob:
    def __init__(self, name: str) -> None:
        self.kernel = _kernel()
        self.handle = _check(self.kernel.CreateJobObjectW(None, name))
        try:
            limits = _Limits()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway permissions.
            _check(
                self.kernel.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
        except BaseException:
            self.close()
            raise

    def active(self) -> int:
        information = _Accounting()
        _check(
            self.kernel.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(information), ctypes.sizeof(information), None
            )
        )
        return information.active

    def terminate_and_wait(self) -> int:
        _check(self.kernel.TerminateJobObject(self.handle, 1))
        deadline = time.monotonic() + 5
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.02)
        remaining = self.active()
        if remaining:
            raise RuntimeError(f"probe_job_cleanup_incomplete: {remaining}")
        return remaining

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def join_before_spawning(name: str) -> None:
    """The worker joins before importing/starting the actual exec-server client."""
    kernel = _kernel()
    handle = _check(kernel.OpenJobObjectW(0x0001, False, name))
    try:
        _check(kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()))
    finally:
        kernel.CloseHandle(handle)


class ObservedProcess:
    """Hold a handle to an already identified fixture, never kill by a saved PID."""

    def __init__(self, pid: int, job_name: str) -> None:
        self.kernel = _kernel()
        self.handle = _check(self.kernel.OpenProcess(0x100000 | 0x1000, False, pid))
        try:
            job = _check(self.kernel.OpenJobObjectW(0x0004, False, job_name))
            try:
                member = wintypes.BOOL()
                _check(self.kernel.IsProcessInJob(self.handle, job, ctypes.byref(member)))
                if not member.value:
                    raise RuntimeError("fixture_outside_probe_job")
            finally:
                self.kernel.CloseHandle(job)
        except BaseException:
            self.close()
            raise

    def alive(self) -> bool:
        status = self.kernel.WaitForSingleObject(self.handle, 0)
        if status not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return status == 258

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
