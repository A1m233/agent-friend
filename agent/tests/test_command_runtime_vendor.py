from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from agent.command_runtime import CodexIdentityError, verify_codex_executable


def test_runtime_verifies_pinned_lock_and_payload_hashes(tmp_path: Path) -> None:
    executable = _payload(tmp_path)
    assert verify_codex_executable(executable) == executable.resolve()


def test_runtime_rejects_self_consistent_identity_when_lock_version_differs(
    tmp_path: Path,
) -> None:
    executable = _payload(tmp_path)
    identity_path = tmp_path / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["version"] = "999.0.0"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    with pytest.raises(CodexIdentityError, match="protocol floor"):
        verify_codex_executable(executable)


def test_runtime_rejects_tampered_executable(tmp_path: Path) -> None:
    executable = _payload(tmp_path)
    executable.write_bytes(b"tampered")
    with pytest.raises(CodexIdentityError, match="executable hash"):
        verify_codex_executable(executable)


def _payload(root: Path) -> Path:
    executable = root / "bin" / "codex.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"codex")
    for name in ("codex-windows-sandbox-setup.exe", "codex-command-runner.exe"):
        (executable.parent / name).write_bytes(name.encode())
    license_path = root / "LICENSE-CODEX"
    license_path.write_bytes(b"license")
    protocol = {
        "transport": "stdio-jsonl",
        "required_methods": ["initialize", "process/start", "process/write", "process/terminate"],
        "required_notifications": ["process/output", "process/exited", "process/closed"],
        "required_start_result": ["sandboxType"],
    }
    lock = {
        "schema_version": 1,
        "product": "openai-codex",
        "version": "1.2.3",
        "release_tag": "rust-v1.2.3",
        "protocol": protocol,
        "license": {"sha256": hashlib.sha256(b"license").hexdigest()},
        "artifacts": {"windows-x86_64": {"sha256": "a" * 64}},
    }
    identity = {
        "schema_version": 1,
        "product": "openai-codex",
        "version": lock["version"],
        "release_tag": lock["release_tag"],
        "platform": "windows-x86_64",
        "archive_sha256": "a" * 64,
        "executable_sha256": hashlib.sha256(b"codex").hexdigest(),
        "bin_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in executable.parent.iterdir()
        },
        "protocol": protocol,
    }
    (root / "vendor-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (root / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    return executable


@pytest.mark.parametrize("action", ["missing", "tampered", "no_manifest"])
def test_runtime_rejects_incomplete_companions(tmp_path: Path, action: str) -> None:
    executable = _payload(tmp_path)
    helper = executable.parent / "codex-windows-sandbox-setup.exe"
    if action == "missing":
        helper.unlink()
    elif action == "tampered":
        helper.write_bytes(b"modified")
    else:
        path = tmp_path / "identity.json"
        data = json.loads(path.read_text())
        data.pop("bin_sha256")
        path.write_text(json.dumps(data))
    with pytest.raises(CodexIdentityError, match="companion"):
        verify_codex_executable(executable)


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_helper_probe_only_reaches_argument_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.command_runtime.vendor import probe_setup_helper

    executable = _payload(tmp_path)
    monkeypatch.setenv("CODEX_HOME", "private-control-state")

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert argv == [str(executable.parent / "codex-windows-sandbox-setup.exe")]
        assert "private-control-state" not in str(kwargs)
        return subprocess.CompletedProcess(
            argv, 1, b"", b"helper_request_args_failed: expected payload argument"
        )

    monkeypatch.setattr("agent.command_runtime.vendor.subprocess.run", run)
    assert probe_setup_helper(executable)["status"] == "ready"
