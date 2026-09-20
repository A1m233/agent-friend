from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent.command_runtime.tools import ExecCommandTool, HostProcessStartTool
from agent.execution_environment import ManagedEnvironmentError
from agent.execution_environment.native import NativeTools


def _payload(root: Path) -> Path:
    root.mkdir(parents=True)
    tools = {
        "pwsh": "powershell/pwsh.exe",
        "pdftoppm": "poppler/pdftoppm.exe",
        "pdfinfo": "poppler/pdfinfo.exe",
    }
    for relative in tools.values():
        p = root / relative
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"test executable")
    lock = b'{"fixture":true}'
    (root / "vendor-lock.json").write_bytes(lock)
    files = [
        {
            "path": p.relative_to(root).as_posix(),
            "size": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in root.rglob("*")
        if p.is_file()
    ]
    (root / "identity.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "identity": hashlib.sha256(lock).hexdigest(),
                "files": files,
                "tools": tools,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_native_payload_checks_content_and_does_not_resolve_external_paths(tmp_path: Path) -> None:
    root = _payload(tmp_path / "native")
    native = NativeTools(root)
    assert native.resolve("PwSh.exe") == root / "powershell/pwsh.exe"
    assert native.resolve("powershell.exe") is None
    assert native.resolve(str(root / "powershell/pwsh.exe")) is None
    assert native.resolve("python") is None
    (root / "powershell/pwsh.exe").write_bytes(b"tampered")
    with pytest.raises(ManagedEnvironmentError, match="产品自带"):
        NativeTools(root).validate()


@pytest.mark.skipif(os.name != "nt", reason="Windows executable resolution")
@pytest.mark.parametrize("tool_type", [ExecCommandTool, HostProcessStartTool])
def test_both_execution_modes_prefer_product_native_tools(
    tmp_path: Path, tool_type: type[ExecCommandTool]
) -> None:
    root = _payload(tmp_path / "native")
    other = tmp_path / "system"
    other.mkdir()
    (other / "pwsh.exe").write_bytes(b"other version")
    tool = tool_type(
        Mock(),
        None,
        base_env={"AGENT_FRIEND_EXECUTION_ENVIRONMENT_ROOT": str(tmp_path), "PATH": str(other)},
    )
    assert tool._structured_argv(("pwsh.exe", "-NoProfile"))[0] == str(root / "powershell/pwsh.exe")
    assert tool._structured_argv((str(other / "pwsh.exe"),))[0] == str(other / "pwsh.exe")
    assert tool._shell_argv('pwsh.exe -NoProfile -Command "Get-Date"', "buffered")[0] == str(
        root / "powershell/pwsh.exe"
    )
    assert tool._child_env(tmp_path)["PATH"].split(os.pathsep)[0] == str(root / "powershell")
    assert tool._host_user_env()["PATH"].split(os.pathsep)[0] == str(root / "powershell")
    assert tool._direct_windows_argv("pdfinfo input.pdf > info.txt", require_existing=True) is None


def test_missing_payload_does_not_fall_back_to_path(tmp_path: Path) -> None:
    with pytest.raises(ManagedEnvironmentError) as caught:
        NativeTools(tmp_path).resolve("pwsh")
    assert caught.value.code == "managed_native_tools_unavailable"
    assert caught.value.provider == "native"


def test_environment_error_reports_the_failed_provider() -> None:
    from agent.command_runtime.tools import _managed_environment_error_result

    for provider in ("native", "python"):
        error = ManagedEnvironmentError("unavailable", "Unavailable", provider=provider)
        result = _managed_environment_error_result(error)
        assert result.is_error
        assert json.loads(result.text)["provider"] == provider
