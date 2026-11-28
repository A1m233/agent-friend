"""Create an owned Windows process inside its Job before any child code can run."""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from collections.abc import Mapping
from ctypes import wintypes
from typing import BinaryIO
from uuid import uuid4

from .windows import _api, _check


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
    _fields_ = [(f"counter_{index}", ctypes.c_uint64) for index in range(6)]


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
        ("user", ctypes.c_int64),
        ("kernel", ctypes.c_int64),
        ("period_user", ctypes.c_int64),
        ("period_kernel", ctypes.c_int64),
        ("faults", wintypes.DWORD),
        ("total", wintypes.DWORD),
        ("active", wintypes.DWORD),
        ("terminated", wintypes.DWORD),
    ]


class _ProcessList(ctypes.Structure):
    _fields_ = [
        ("assigned", wintypes.DWORD),
        ("count", wintypes.DWORD),
        ("pids", ctypes.c_size_t * 1024),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("xsize", wintypes.DWORD),
        ("ysize", wintypes.DWORD),
        ("xchars", wintypes.DWORD),
        ("ychars", wintypes.DWORD),
        ("fill", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_ptr", ctypes.c_void_p),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD),
        ("tid", wintypes.DWORD),
    ]


class OwnedWindowsProcess:
    """Popen's small binary-pipe interface, with a non-breakaway Job owner.

    The Job proves membership/closure of its own processes. It does not claim
    ownership of work launched by UAC or other external brokers.
    """

    def __init__(
        self, command: list[str], *, env: Mapping[str, str], cwd: str | None = None
    ) -> None:
        import msvcrt

        self.command = command
        self._service_handles: dict[int, int] = {}
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._close_handle = _api(kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
        self._wait = _api(
            kernel, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD
        )
        self._exit = _api(
            kernel,
            "GetExitCodeProcess",
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        )
        self._terminate_job = _api(
            kernel, "TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL
        )
        self._query = _api(
            kernel,
            "QueryInformationJobObject",
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        )
        self.job_name = "Local\\AgentFriendCommand_" + uuid4().hex
        create_job = _api(
            kernel, "CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE
        )
        self.job = _check(create_job(None, self.job_name))
        self.handle: int | None = None
        self.stdin: BinaryIO | None = None
        self.stdout: BinaryIO | None = None
        self.stderr: BinaryIO | None = None
        self.returncode: int | None = None
        pipes: list[int] = []
        attributes = None
        delete_attributes = _api(kernel, "DeleteProcThreadAttributeList", [ctypes.c_void_p], None)
        try:
            limits = _Limits()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway.
            set_job = _api(
                kernel,
                "SetInformationJobObject",
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            )
            _check(set_job(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            input_read, input_write = os.pipe()
            pipes.extend((input_read, input_write))
            output_read, output_write = os.pipe()
            pipes.extend((output_read, output_write))
            error_read, error_write = os.pipe()
            pipes.extend((error_read, error_write))
            children = [input_read, output_write, error_write]
            for descriptor in children:
                os.set_inheritable(descriptor, True)
            handles = (wintypes.HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in children))
            jobs = (wintypes.HANDLE * 1)(self.job)
            size = ctypes.c_size_t()
            initialize = _api(
                kernel,
                "InitializeProcThreadAttributeList",
                [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)],
                wintypes.BOOL,
            )
            initialize(None, 2, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            _check(initialize(attributes, 2, 0, ctypes.byref(size)))
            update = _api(
                kernel,
                "UpdateProcThreadAttribute",
                [
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            )
            _check(update(attributes, 0, 0x20002, handles, ctypes.sizeof(handles), None, None))
            # PROC_THREAD_ATTRIBUTE_JOB_LIST is applied atomically by CreateProcess.
            _check(update(attributes, 0, 0x2000D, jobs, ctypes.sizeof(jobs), None, None))
            startup = _StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
            information = _ProcessInfo()
            create = _api(
                kernel,
                "CreateProcessW",
                [
                    wintypes.LPCWSTR,
                    wintypes.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    wintypes.BOOL,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.LPCWSTR,
                    ctypes.POINTER(_StartupInfoEx),
                    ctypes.POINTER(_ProcessInfo),
                ],
                wintypes.BOOL,
            )
            environment = ctypes.create_unicode_buffer(
                "\0".join(
                    f"{key}={value}"
                    for key, value in sorted(env.items(), key=lambda item: item[0].upper())
                )
                + "\0"
            )
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            _check(
                create(
                    command[0],
                    command_line,
                    None,
                    None,
                    True,
                    0x08000000 | 0x00080000 | 0x400,
                    environment,
                    cwd,
                    ctypes.byref(startup),
                    ctypes.byref(information),
                )
            )
            self.handle, self.pid = information.process, information.pid
            self._close_handle(information.thread)
            self.stdin = os.fdopen(input_write, "wb", buffering=0)
            pipes.remove(input_write)
            self.stdout = os.fdopen(output_read, "rb", buffering=0)
            pipes.remove(output_read)
            self.stderr = os.fdopen(error_read, "rb", buffering=0)
            pipes.remove(error_read)
        except BaseException:
            self.close()
            raise
        finally:
            for descriptor in pipes:
                os.close(descriptor)
            if attributes is not None:
                delete_attributes(attributes)

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        result = self._wait(self.handle, 0)
        if result == 258:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        _check(self._exit(self.handle, ctypes.byref(code)))
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + timeout if timeout is not None else float("inf")
        while (code := self.poll()) is None:
            if time.monotonic() >= deadline:
                assert timeout is not None
                raise subprocess.TimeoutExpired(self.command, timeout)
            time.sleep(0.01)
        return code

    def active_count(self) -> int:
        if not self.job:
            raise OSError("job_owner_closed")
        value = _Accounting()
        _check(self._query(self.job, 1, ctypes.byref(value), ctypes.sizeof(value), None))
        return int(value.active)

    def _members(self) -> set[int]:
        if not self.job:
            raise OSError("job_owner_closed")
        members = _ProcessList()
        _check(self._query(self.job, 3, ctypes.byref(members), ctypes.sizeof(members), None))
        if members.assigned > 1024 or members.count > 1024:
            raise OSError("job_membership_capacity_exceeded")
        return set(members.pids[: members.count])

    def mark_service_ready(self) -> None:
        """Pin service/launcher identities before the first business request."""
        if self._service_handles:
            raise OSError("service_identity_already_captured")
        open_process = _api(
            self.kernel,
            "OpenProcess",
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
            wintypes.HANDLE,
        )
        for pid in self._members():
            handle = _check(open_process(0x00100000, False, pid))  # SYNCHRONIZE only
            self._service_handles[pid] = handle

    def only_service_processes(self) -> bool:
        # Live retained handles, not a count or a reusable PID, are the baseline.
        live = {
            pid for pid, handle in self._service_handles.items() if self._wait(handle, 0) == 258
        }
        return self.poll() is None and bool(live) and self._members() <= live

    def terminate(self) -> None:
        terminate = _api(
            self.kernel, "TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL
        )
        _check(terminate(self.handle, 1))

    kill = terminate

    def stop_and_wait(self, timeout: float = 5) -> bool:
        _check(self._terminate_job(self.job, 1))
        return self.wait_for_owned_exit(timeout)

    def wait_for_owned_exit(self, timeout: float = 5) -> bool:
        deadline = time.monotonic() + timeout
        while self.active_count() and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.active_count() == 0

    def close(self) -> None:
        for handle in self._service_handles.values():
            self._close_handle(handle)
        self._service_handles.clear()
        if self.job:
            self._close_handle(self.job)
            self.job = None
        if self.handle:
            self._close_handle(self.handle)
            self.handle = None
        for pipe in (self.stdin, self.stdout, self.stderr):
            if pipe is not None:
                pipe.close()
