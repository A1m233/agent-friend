"""Regression for Codex re-provisioning a marker without the product user's ACE."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

import pytest
from agent.command_runtime import CommandRuntime, CommandRuntimeError


def test_executor_identity_preserved_without_forwarding_credentials(tmp_path: Path) -> None:
    runtime = CommandRuntime(
        tmp_path / "codex.exe",
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "work",
        base_env={
            "username": "alice",
            "userdomain": "EXAMPLE",
            "OPENAI_API_KEY": "private",
            "CODEX_ACCESS_TOKEN": "private",
            "AGENT_FRIEND_INTERNAL_TOKEN": "private",
            "HTTPS_PROXY": "http://user:password@proxy",
        },
    )
    assert runtime._exec_server_env() == {
        "USERNAME": "alice",
        "USERDOMAIN": "EXAMPLE",
        "CODEX_HOME": str(tmp_path / "home"),
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox identity requirement")
@pytest.mark.parametrize("username", [None, "", "  "])
def test_missing_identity_rejected_before_executor_start(
    tmp_path: Path,
    username: str | None,
) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"must-not-execute")
    env = {"SystemRoot": "C:\\Windows"}
    if username is not None:
        env["USERNAME"] = username
    runtime = CommandRuntime(
        executable,
        codex_home=tmp_path / "home",
        execution_root=tmp_path / "work",
        base_env=env,
    )
    with pytest.raises(CommandRuntimeError, match="signed-in user identity") as exc:
        runtime._ensure_client()
    assert exc.value.code == "command_runtime_unavailable"
    assert runtime._client is None


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ACL access check")
def test_recreated_marker_acl_remains_readable_by_unelevated_user(tmp_path: Path) -> None:
    # Match the pinned helper's protected marker DACL in memory. No files,
    # accounts or system ACLs are changed, and no elevated helper is launched.
    security = pytest.importorskip("win32security")
    api = pytest.importorskip("win32api")
    constants = pytest.importorskip("win32con")
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32")

    class Mapping(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in ("read", "write", "execute", "all")]

    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    adv.AccessCheck.argtypes = [
        ctypes.c_void_p,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(Mapping),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token = security.OpenProcessToken(
        api.GetCurrentProcess(),
        constants.TOKEN_QUERY | constants.TOKEN_DUPLICATE,
    )
    admin = security.CreateWellKnownSid(security.WinBuiltinAdministratorsSid, None)
    restricted = security.CreateRestrictedToken(token, 0, [(admin, 0)], [], [])
    impersonation = security.DuplicateToken(restricted, security.SecurityImpersonation)
    try:
        user_sid = security.GetTokenInformation(token, security.TokenUser)[0]
        name, domain, _ = security.LookupAccountSid(None, user_sid)
        runtime = CommandRuntime(
            tmp_path / "codex.exe",
            codex_home=tmp_path / "home",
            execution_root=tmp_path / "work",
            base_env={"USERNAME": name, "USERDOMAIN": domain},
        )
        env = runtime._exec_server_env()
        selected_sid = security.LookupAccountName(None, env.get("USERNAME", "Administrators"))[0]
        for principal, expected in [(admin, False), (selected_sid, True)]:
            sid = security.ConvertSidToStringSid(principal)
            # FA is GA after file-object generic mapping. Owner/group are supplied
            # because AccessCheck requires a complete descriptor.
            sddl = f"O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})"
            descriptor = ctypes.c_void_p()
            size = wintypes.DWORD()
            assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl,
                1,
                ctypes.byref(descriptor),
                ctypes.byref(size),
            )
            try:
                mapping = Mapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
                privileges = ctypes.create_string_buffer(4096)
                length = wintypes.DWORD(4096)
                granted = wintypes.DWORD()
                allowed = wintypes.BOOL()
                assert adv.AccessCheck(
                    descriptor,
                    int(impersonation),
                    1,
                    ctypes.byref(mapping),
                    privileges,
                    ctypes.byref(length),
                    ctypes.byref(granted),
                    ctypes.byref(allowed),
                ), ctypes.get_last_error()
                assert bool(allowed.value) is expected
            finally:
                kernel.LocalFree(descriptor)
    finally:
        impersonation.Close()
        restricted.Close()
        token.Close()
