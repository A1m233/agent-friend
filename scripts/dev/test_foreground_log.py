from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "foreground_log_under_test", Path(__file__).with_name("foreground_log.py")
)
assert spec and spec.loader
logging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logging)


def test_old_giant_log_is_retained_seven_days_and_new_output_rotates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "dev-bridge.log"
    path.write_bytes(b"historical" * 20)
    monkeypatch.setattr(logging.time, "time", lambda: 100)
    with logging.RotatingBinaryLog(path, max_bytes=10, keep_files=3) as sink:
        sink.write(b"x" * 53)
    old = next(tmp_path.glob("*.legacy-*"))
    assert old.read_bytes() == b"historical" * 20
    assert all(
        p.stat().st_size <= 10
        for p in [path, tmp_path / "dev-bridge.log.1", tmp_path / "dev-bridge.log.2"]
    )
    monkeypatch.setattr(logging.time, "time", lambda: 100 + 7 * 86400 - 1)
    with logging.RotatingBinaryLog(path, max_bytes=10, keep_files=3):
        pass
    assert old.exists()
    monkeypatch.setattr(logging.time, "time", lambda: 100 + 7 * 86400)
    with logging.RotatingBinaryLog(path, max_bytes=10, keep_files=3):
        pass
    assert not old.exists()


def test_disk_failure_keeps_draining_child_without_unbounded_line_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = logging.RotatingBinaryLog.write
    writes = 0
    captured: list[int] = []

    def fail_after_header(self, data):
        nonlocal writes
        writes += 1
        if writes > 1:
            raise OSError("disk full")
        return original(self, data)

    monkeypatch.setattr(logging.RotatingBinaryLog, "write", fail_after_header)
    monkeypatch.setattr(
        logging, "_write_console", lambda data, _prefix, _state: captured.append(len(data)) or False
    )
    code = logging.run_logged(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*1048576)"],
        cwd=tmp_path,
        log_path=tmp_path / "dev.log",
    )
    assert code == 0
    assert sum(captured) == 1048576
    assert max(captured) <= 64 * 1024
