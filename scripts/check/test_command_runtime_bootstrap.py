from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "command-runtime" / "run.py"
SPEC = importlib.util.spec_from_file_location("agent_friend_command_runtime_runner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
VENDOR_CODEX = sys.modules[MODULE.prepare.__module__]


def test_source_cache_is_keyed_by_vendor_lock_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "codex.lock.json"
    lock_path.write_text('{"version":"one"}\n', encoding="utf-8")
    monkeypatch.setattr(MODULE, "LOCK_PATH", lock_path)
    monkeypatch.setattr(MODULE, "_source_cache_root", lambda: tmp_path / "shared")

    first = MODULE._source_cache_base()
    lock_path.write_text('{"version":"two"}\n', encoding="utf-8")
    second = MODULE._source_cache_base()

    assert first.parent == second.parent == tmp_path / "shared" / "codex"
    assert first != second
    assert len(first.name) == len(second.name) == 64


def test_verify_source_runtime_never_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "codex.exe"
    cache_base = tmp_path / "shared" / "identity"
    calls: list[tuple[Path, Path, bool]] = []
    monkeypatch.setattr(MODULE, "_source_cache_base", lambda: cache_base)

    def fake_prepare(
        repo_root: Path,
        *,
        cache_base: Path,
        offline: bool = False,
    ) -> Path:
        calls.append((repo_root, cache_base, offline))
        return executable

    monkeypatch.setattr(MODULE, "prepare", fake_prepare)

    assert MODULE._verify_source_runtime(tmp_path / "worktree") == executable
    assert calls == [(tmp_path / "worktree", cache_base, True)]


def test_ensure_returns_cache_hit_without_taking_prepare_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "codex.exe"
    monkeypatch.setattr(MODULE, "_verify_source_runtime", lambda _root: executable)

    def unexpected_lock(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cache hit must not take the prepare lock")

    monkeypatch.setattr(MODULE, "_exclusive_cache_lock", unexpected_lock)

    assert MODULE.ensure_source_runtime(tmp_path / "worktree") == executable


def test_ensure_prepares_missing_cache_under_the_shared_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "shared" / "identity" / "codex-0-platform" / "bin" / "codex.exe"
    cache_base = tmp_path / "shared" / "identity"
    calls: list[tuple[Path, Path, bool]] = []
    monkeypatch.setattr(MODULE, "_source_cache_base", lambda: cache_base)
    monkeypatch.setattr(
        MODULE,
        "_verify_source_runtime",
        lambda _root: (_ for _ in ()).throw(RuntimeError("missing")),
    )

    def fake_prepare(
        repo_root: Path,
        *,
        cache_base: Path,
        offline: bool = False,
    ) -> Path:
        calls.append((repo_root, cache_base, offline))
        return executable

    monkeypatch.setattr(MODULE, "prepare", fake_prepare)

    assert MODULE.ensure_source_runtime(tmp_path / "worktree") == executable
    assert calls == [(tmp_path / "worktree", cache_base, False)]


def test_same_lock_is_reused_across_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_base = tmp_path / "shared" / "identity"
    executable = cache_base / "codex-0-platform" / "bin" / "codex.exe"
    prepared = threading.Event()
    prepare_roots: list[Path] = []
    monkeypatch.setattr(MODULE, "_source_cache_base", lambda: cache_base)

    def fake_verify(_repo_root: Path) -> Path:
        if not prepared.is_set():
            raise RuntimeError("missing")
        return executable

    def fake_prepare(
        repo_root: Path,
        *,
        cache_base: Path,
        offline: bool = False,
    ) -> Path:
        assert cache_base == tmp_path / "shared" / "identity"
        assert offline is False
        prepare_roots.append(repo_root)
        prepared.set()
        return executable

    monkeypatch.setattr(MODULE, "_verify_source_runtime", fake_verify)
    monkeypatch.setattr(MODULE, "prepare", fake_prepare)

    first_root = tmp_path / "worktree-one"
    second_root = tmp_path / "worktree-two"
    assert MODULE.ensure_source_runtime(first_root) == executable
    assert MODULE.ensure_source_runtime(second_root) == executable
    assert prepare_roots == [first_root]


def test_concurrent_ensure_prepares_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_base = tmp_path / "shared" / "identity"
    executable = cache_base / "codex-0-platform" / "bin" / "codex.exe"
    prepared = threading.Event()
    prepare_calls = 0
    prepare_calls_guard = threading.Lock()
    monkeypatch.setattr(MODULE, "_source_cache_base", lambda: cache_base)

    def fake_verify(_repo_root: Path) -> Path:
        if not prepared.is_set():
            raise RuntimeError("missing")
        return executable

    def fake_prepare(
        _repo_root: Path,
        *,
        cache_base: Path,
        offline: bool = False,
    ) -> Path:
        nonlocal prepare_calls
        assert cache_base == tmp_path / "shared" / "identity"
        assert offline is False
        with prepare_calls_guard:
            prepare_calls += 1
        time.sleep(0.1)
        prepared.set()
        return executable

    monkeypatch.setattr(MODULE, "_verify_source_runtime", fake_verify)
    monkeypatch.setattr(MODULE, "prepare", fake_prepare)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(MODULE.ensure_source_runtime, [tmp_path / "one", tmp_path / "two"])
        )

    assert results == [executable, executable]
    assert prepare_calls == 1


def test_download_failure_does_not_publish_a_partial_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_base = tmp_path / "shared" / "identity"
    lock = VENDOR_CODEX.load_lock()
    destination = cache_base / f"codex-{lock['version']}-{VENDOR_CODEX.platform_key()}"

    def fail_download(_url: str, _destination: Path, *, offline: bool = False) -> None:
        assert offline is False
        raise OSError("network unavailable")

    monkeypatch.setattr(VENDOR_CODEX, "_download", fail_download)

    with pytest.raises(OSError, match="network unavailable"):
        VENDOR_CODEX.prepare(ROOT, cache_base=cache_base)

    assert not destination.exists()


def test_cache_lock_serializes_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "runtime.lock"
    log_path = tmp_path / "critical-sections.log"
    code = """
import importlib.util
import os
from pathlib import Path
import sys
import time

spec = importlib.util.spec_from_file_location("command_runtime_lock_probe", Path(sys.argv[1]))
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
with module._exclusive_cache_lock(Path(sys.argv[2]), timeout_seconds=10):
    with Path(sys.argv[3]).open("a", encoding="utf-8") as output:
        output.write(f"start {os.getpid()}\\n")
        output.flush()
    time.sleep(0.2)
    with Path(sys.argv[3]).open("a", encoding="utf-8") as output:
        output.write(f"end {os.getpid()}\\n")
        output.flush()
"""
    command = [sys.executable, "-c", code, str(MODULE_PATH), str(lock_path), str(log_path)]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_stdout, first_stderr = first.communicate(timeout=15)
    second_stdout, second_stderr = second.communicate(timeout=15)

    assert (first.returncode, first_stdout, first_stderr) == (0, "", "")
    assert (second.returncode, second_stdout, second_stderr) == (0, "", "")
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    for index in (0, 2):
        start_kind, start_pid = lines[index].split()
        end_kind, end_pid = lines[index + 1].split()
        assert (start_kind, end_kind) == ("start", "end")
        assert start_pid == end_pid


def test_ensure_reports_download_failure_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        MODULE,
        "ensure_source_runtime",
        lambda _root: (_ for _ in ()).throw(OSError("network unavailable")),
    )

    assert MODULE.main(["ensure"]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "OSError: network unavailable"
    assert captured.err == ""


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/setup/run.ps1",
        "scripts/setup/run.sh",
        "scripts/dev/run.ps1",
        "scripts/dev/run.sh",
        "scripts/bridge/run.ps1",
        "scripts/bridge/run.sh",
    ],
)
def test_standard_entrypoints_ensure_the_command_runtime(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8-sig")

    assert "scripts/command-runtime/run.py ensure" in text
    assert "path --quiet" not in text


@pytest.mark.parametrize("relative_path", ["scripts/dev/run.ps1", "scripts/dev/run.sh"])
def test_dev_status_and_stop_return_before_ensure(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8-sig")

    ensure_index = text.index("scripts/command-runtime/run.py ensure")
    assert text.index('"status"') < ensure_index or text.index('= "status"') < ensure_index
    assert text.index('"stop"') < ensure_index or text.index('= "stop"') < ensure_index
