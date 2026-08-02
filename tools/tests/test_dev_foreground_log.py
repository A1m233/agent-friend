from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "dev" / "foreground_log.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dev_foreground_log_for_tests", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def test_rotating_log_keeps_fixed_file_count(tmp_path: Path) -> None:
    log_path = tmp_path / "dev-tauri.log"
    with MODULE.RotatingBinaryLog(log_path, max_bytes=10, keep_files=3) as sink:
        sink.write(b"0123456789abcdefghijABCDEFGHIJ")

    assert log_path.read_bytes() == b"ABCDEFGHIJ"
    assert (tmp_path / "dev-tauri.log.1").read_bytes() == b"abcdefghij"
    assert (tmp_path / "dev-tauri.log.2").read_bytes() == b"0123456789"
    assert not (tmp_path / "dev-tauri.log.3").exists()


def test_logged_process_merges_stderr_and_records_exit_code(tmp_path: Path) -> None:
    log_path = tmp_path / "dev-tauri.log"
    exit_code = MODULE.run_logged(
        [
            sys.executable,
            "-c",
            "import sys; print('stdout-marker'); print('stderr-marker', file=sys.stderr); sys.exit(7)",
        ],
        cwd=tmp_path,
        log_path=log_path,
        max_bytes=4096,
        keep_files=2,
    )

    content = log_path.read_text(encoding="utf-8")
    assert exit_code == 7
    assert "stdout-marker" in content
    assert "stderr-marker" in content
    assert "exit: code=7" in content
