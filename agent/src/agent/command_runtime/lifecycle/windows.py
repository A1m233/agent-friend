"""Small native boundaries: protected journal storage, uptime and maintenance mutex."""

from __future__ import annotations

import ctypes
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any


def uptime_ms() -> int | None:
    if os.name != "nt":
        return None
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetTickCount64
    function.restype = ctypes.c_uint64
    return int(function())


def _api(library: Any, name: str, args: list[Any], result: Any) -> Any:
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


def _check(value: Any) -> Any:
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def protect_directory(directory: Path) -> None:
    for path in (*reversed(directory.absolute().parents), directory.absolute()):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("lifecycle storage must not traverse a reparse point")
    directory.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.mkdir(mode=0o700, exist_ok=True)
        metadata = directory.stat()
        getuid = getattr(os, "getuid", None)
        if getuid is None or metadata.st_uid != getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("lifecycle directory is not private")
        return
    _windows_directory(directory)


def _windows_directory(directory: Path) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    local_free = _api(kernel, "LocalFree", [pointer], pointer)
    close = _api(kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
    current = _api(kernel, "GetCurrentProcess", [], wintypes.HANDLE)
    open_token = _api(
        security,
        "OpenProcessToken",
        [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
        wintypes.BOOL,
    )
    token_info = _api(
        security,
        "GetTokenInformation",
        [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
        wintypes.BOOL,
    )
    sid_text = _api(
        security,
        "ConvertSidToStringSidW",
        [pointer, ctypes.POINTER(wintypes.LPWSTR)],
        wintypes.BOOL,
    )

    def sid_string(sid: Any) -> str:
        text = wintypes.LPWSTR()
        _check(sid_text(sid, ctypes.byref(text)))
        try:
            return str(text.value)
        finally:
            local_free(text)

    token = wintypes.HANDLE()
    _check(open_token(current(), 0x0008, ctypes.byref(token)))
    try:
        needed = wintypes.DWORD()
        token_info(token, 1, None, 0, ctypes.byref(needed))
        data = ctypes.create_string_buffer(needed.value)
        _check(token_info(token, 1, data, needed.value, ctypes.byref(needed)))
        owner_sid = sid_string(pointer.from_buffer(data).value)
    finally:
        close(token)
    descriptor = pointer()
    convert = _api(
        security,
        "ConvertStringSecurityDescriptorToSecurityDescriptorW",
        [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer],
        wintypes.BOOL,
    )
    _check(
        convert(
            f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{owner_sid})",
            1,
            ctypes.byref(descriptor),
            None,
        )
    )

    class Attributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", pointer), ("inherit", wintypes.BOOL)]

    attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
    create = _api(
        kernel, "CreateDirectoryW", [wintypes.LPCWSTR, ctypes.POINTER(Attributes)], wintypes.BOOL
    )
    try:
        if not create(str(directory), ctypes.byref(attributes)) and ctypes.get_last_error() != 183:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        local_free(descriptor)
    # Existing data is accepted only under the same protection. Tightening a
    # writable directory and then trusting its old contents would be unsafe.
    get_security = _api(
        security,
        "GetFileSecurityW",
        [wintypes.LPCWSTR, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
        wintypes.BOOL,
    )
    needed = wintypes.DWORD()
    get_security(str(directory), 5, None, 0, ctypes.byref(needed))
    if not 0 < needed.value <= 65536:
        raise ValueError("invalid lifecycle directory security")
    data = ctypes.create_string_buffer(needed.value)
    _check(get_security(str(directory), 5, data, needed.value, ctypes.byref(needed)))
    control, revision = wintypes.WORD(), wintypes.DWORD()
    get_control = _api(
        security,
        "GetSecurityDescriptorControl",
        [pointer, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)],
        wintypes.BOOL,
    )
    _check(get_control(data, ctypes.byref(control), ctypes.byref(revision)))
    if not control.value & 0x1000:
        raise ValueError("lifecycle DACL must be protected")
    get_owner = _api(
        security,
        "GetSecurityDescriptorOwner",
        [pointer, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL)],
        wintypes.BOOL,
    )
    owner, defaulted = pointer(), wintypes.BOOL()
    _check(get_owner(data, ctypes.byref(owner), ctypes.byref(defaulted)))
    if sid_string(owner) not in {owner_sid, "S-1-5-18", "S-1-5-32-544"}:
        raise ValueError("untrusted lifecycle owner")
    get_dacl = _api(
        security,
        "GetSecurityDescriptorDacl",
        [
            pointer,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(pointer),
            ctypes.POINTER(wintypes.BOOL),
        ],
        wintypes.BOOL,
    )
    acl, present = pointer(), wintypes.BOOL()
    _check(get_dacl(data, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)))
    if not present.value or not acl.value:
        raise ValueError("lifecycle DACL is missing")
    count = ctypes.c_ushort.from_address(acl.value + 4).value
    get_ace = _api(
        security, "GetAce", [pointer, wintypes.DWORD, ctypes.POINTER(pointer)], wintypes.BOOL
    )
    for index in range(count):
        ace = pointer()
        _check(get_ace(acl, index, ctypes.byref(ace)))
        assert ace.value is not None
        kind = ctypes.c_ubyte.from_address(ace.value).value
        if kind == 1:  # A deny ACE cannot grant access.
            continue
        if kind != 0 or sid_string(ace.value + 8) not in {owner_sid, "S-1-5-18", "S-1-5-32-544"}:
            raise ValueError("untrusted lifecycle access grant")


@contextmanager
def maintenance_mutex() -> Iterator[None]:
    """Machine-wide identity maintenance; no guessing based on another PID."""
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = _api(
        kernel, "CreateMutexW", [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE
    )
    wait = _api(kernel, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
    release = _api(kernel, "ReleaseMutex", [wintypes.HANDLE], wintypes.BOOL)
    close = _api(kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
    handle = _check(create(None, False, "Global\\AgentFriendCodexSandboxMaintenance"))
    acquired = False
    try:
        result = wait(handle, 0)
        if result not in {0, 0x80}:
            raise OSError("sandbox_maintenance_busy")
        acquired = True
        if result == 0x80:
            raise OSError("sandbox_maintenance_previous_owner_lost")
        yield
    finally:
        if acquired:
            release(handle)
        close(handle)
