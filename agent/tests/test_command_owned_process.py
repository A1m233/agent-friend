from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from agent.command_runtime.lifecycle.process import OwnedWindowsProcess


@pytest.mark.skipif(os.name != "nt", reason="Windows native Job ownership")
def test_process_is_contained_before_start_and_descendants_survive_normal_root_exit() -> None:
    code = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(p.pid,flush=True)"
    )
    process = OwnedWindowsProcess([sys.executable, "-c", code], env=os.environ)
    try:
        assert process.stdout is not None
        assert int(process.stdout.readline()) > 0
        assert process.wait(timeout=5) == 0
        # Normal root exit preserves the descendant; only explicit scope
        # cleanup terminates it. Root exit/pipe status cannot stand in for this.
        assert process.active_count() >= 1
        assert process.stop_and_wait(timeout=5)
        assert process.wait_for_owned_exit(timeout=5)
    finally:
        process.stop_and_wait(timeout=5)
        process.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native Job ownership")
def test_owned_process_transports_unicode_and_stdin() -> None:
    process = OwnedWindowsProcess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.readline())"],
        env=os.environ,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("isolated-测试\n".encode())
        assert process.stdout.readline() == "isolated-测试\n".encode()
        assert process.wait(timeout=5) == 0
        assert process.stop_and_wait(timeout=5)
    finally:
        process.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native logon broker")
def test_logon_broker_preserves_job_membership_without_real_credentials() -> None:
    fixture = Path(__file__).parent / "fixtures/job_logon_probe.py"
    process = OwnedWindowsProcess([sys.executable, str(fixture)], env=os.environ)
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write((process.job_name + "\n").encode())
        result = json.loads(process.stdout.readline())
        assert result == {"created": True, "queried": True, "member": True}
        assert process.wait(timeout=10) == 0
        assert process.wait_for_owned_exit(timeout=5)
    finally:
        process.stop_and_wait(timeout=5)
        process.close()
