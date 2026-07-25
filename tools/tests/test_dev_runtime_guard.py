from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from threading import Thread
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "scripts" / "dev" / "runtime_guard.py"


def load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("dev_runtime_guard_for_tests", GUARD_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = load_guard()


def make_context(tmp_path: Path) -> Any:
    return guard.RuntimeContext(
        worktree=tmp_path / "worktrees" / "other-parent" / "agent-friend",
        mode="tauri",
        ports=[1420, 1421, 18800, 18900],
        repo_id="https://github.com/a1m233/agent-friend",
        branch="fix/dev-runtime-single-owner",
    )


def write_lease(tmp_path: Path, *, pid: int, start_id: str, worktree: str = "old") -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "lease.json").write_text(
        json.dumps(
            {
                "version": 1,
                "ownerPid": pid,
                "ownerStartId": start_id,
                "worktree": worktree,
                "gitRepo": "https://github.com/a1m233/agent-friend",
                "branch": "fix/other",
                "mode": "tauri",
                "startedAt": "2026-07-13T00:00:00+00:00",
                "ports": [1420, 1421, 18800, 18900],
            }
        ),
        encoding="utf-8",
    )


def configure_guard(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    processes: list[Any],
    ports: dict[int, list[int]] | None = None,
) -> list[int]:
    stopped: list[int] = []

    def stop_process_tree(pid: int) -> bool:
        stopped.append(pid)
        return True

    monkeypatch.setattr(guard, "runtime_state_dir", lambda: tmp_path)
    monkeypatch.setattr(guard, "list_processes", lambda: list(processes))
    monkeypatch.setattr(
        guard,
        "port_owners",
        lambda requested: {port: list((ports or {}).get(port, [])) for port in requested},
    )
    monkeypatch.setattr(guard, "stop_process_tree", stop_process_tree)
    return stopped


def process(pid: int, start_id: str, command: str, ppid: int = 0) -> Any:
    return guard.ProcessInfo(
        pid=pid,
        ppid=ppid,
        name=Path(command.split()[0]).name,
        command=command,
        executable=command.split()[0],
        start_id=start_id,
    )


def test_active_owner_blocks_normal_acquire(monkeypatch: Any, tmp_path: Path) -> None:
    configure_guard(monkeypatch, tmp_path, processes=[process(111, "owner-a", "pwsh run.ps1")])
    write_lease(tmp_path, pid=111, start_id="owner-a", worktree="C:/wt/agent-friend")

    result = guard.RuntimeManager(make_context(tmp_path)).acquire(takeover=False, owner_pid=222)

    assert result.code == 1
    assert "普通启动不会抢占" in "\n".join(result.lines)
    lease = json.loads((tmp_path / "lease.json").read_text(encoding="utf-8"))
    assert lease["ownerPid"] == 111


def test_stale_lease_is_recovered_before_acquire(monkeypatch: Any, tmp_path: Path) -> None:
    configure_guard(monkeypatch, tmp_path, processes=[process(222, "owner-b", "pwsh run.ps1")])
    write_lease(tmp_path, pid=111, start_id="dead-owner")

    result = guard.RuntimeManager(make_context(tmp_path)).acquire(takeover=False, owner_pid=222)

    assert result.code == 0
    assert "stale lease" in "\n".join(result.lines)
    lease = json.loads((tmp_path / "lease.json").read_text(encoding="utf-8"))
    assert lease["ownerPid"] == 222


def test_pid_reuse_does_not_count_as_active_owner(monkeypatch: Any, tmp_path: Path) -> None:
    configure_guard(
        monkeypatch,
        tmp_path,
        processes=[
            process(111, "new-process-same-pid", "python unrelated.py"),
            process(222, "owner-b", "pwsh run.ps1"),
        ],
    )
    write_lease(tmp_path, pid=111, start_id="old-process")

    result = guard.RuntimeManager(make_context(tmp_path)).acquire(takeover=False, owner_pid=222)

    assert result.code == 0
    lease = json.loads((tmp_path / "lease.json").read_text(encoding="utf-8"))
    assert lease["ownerPid"] == 222
    assert lease["ownerStartId"] == "owner-b"


def test_unknown_port_owner_is_never_stopped_on_acquire(monkeypatch: Any, tmp_path: Path) -> None:
    stopped = configure_guard(
        monkeypatch,
        tmp_path,
        processes=[process(333, "server", "python -m http.server 1420")],
        ports={1420: [333]},
    )

    result = guard.RuntimeManager(make_context(tmp_path)).acquire(takeover=False, owner_pid=222)

    assert result.code == 1
    assert "未知/非本仓库进程占用" in "\n".join(result.lines)
    assert stopped == []
    assert not (tmp_path / "lease.json").exists()


def test_agent_bridge_port_owner_is_classified_as_same_repo_runtime(
    monkeypatch: Any, tmp_path: Path
) -> None:
    configure_guard(
        monkeypatch,
        tmp_path,
        processes=[process(333, "bridge", r"C:\Python312\python.exe -m agent_bridge")],
        ports={18800: [333]},
    )

    result = guard.RuntimeManager(make_context(tmp_path)).status()

    assert result.code == 0
    assert "port=18800 PID=333 class=same-repo-legacy" in "\n".join(result.lines)


def test_other_worktree_legacy_runtime_requires_takeover(monkeypatch: Any, tmp_path: Path) -> None:
    legacy = process(
        333,
        "vite",
        r"C:\worktrees\someone-else\agent-friend\frontend\node_modules\.bin\vite.js",
    )
    stopped = configure_guard(
        monkeypatch,
        tmp_path,
        processes=[legacy, process(222, "owner-b", "pwsh run.ps1")],
        ports={1420: [333]},
    )
    manager = guard.RuntimeManager(make_context(tmp_path))

    normal = manager.acquire(takeover=False, owner_pid=222)
    takeover = manager.acquire(takeover=True, owner_pid=222)

    assert normal.code == 1
    assert "legacy dev runtime" in "\n".join(normal.lines)
    assert takeover.code == 0
    assert stopped == [333]
    lease = json.loads((tmp_path / "lease.json").read_text(encoding="utf-8"))
    assert lease["ownerPid"] == 222


def test_concurrent_acquire_allows_only_one_owner(monkeypatch: Any, tmp_path: Path) -> None:
    configure_guard(
        monkeypatch,
        tmp_path,
        processes=[
            process(101, "owner-a", "pwsh run.ps1"),
            process(202, "owner-b", "pwsh run.ps1"),
        ],
    )
    results: list[Any] = []

    def acquire(owner_pid: int) -> None:
        results.append(
            guard.RuntimeManager(make_context(tmp_path)).acquire(
                takeover=False, owner_pid=owner_pid
            )
        )

    threads = [Thread(target=acquire, args=(101,)), Thread(target=acquire, args=(202,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.code for result in results) == [0, 1]
    lease = json.loads((tmp_path / "lease.json").read_text(encoding="utf-8"))
    assert lease["ownerPid"] in {101, 202}


def test_status_stop_and_release(monkeypatch: Any, tmp_path: Path) -> None:
    stopped = configure_guard(
        monkeypatch, tmp_path, processes=[process(111, "owner-a", "bash run.sh")]
    )
    write_lease(tmp_path, pid=111, start_id="owner-a", worktree="/tmp/wt/agent-friend")
    manager = guard.RuntimeManager(make_context(tmp_path))

    status = manager.status()
    stop = manager.stop()
    write_lease(tmp_path, pid=111, start_id="owner-a", worktree="/tmp/wt/agent-friend")
    release = manager.release(111)

    assert status.code == 0
    assert "active agent-friend dev runtime" in "\n".join(status.lines)
    assert stop.code == 0
    assert stopped == [111]
    assert release.code == 0
    assert not (tmp_path / "lease.json").exists()


def test_release_cli_accepts_runtime_context_and_removes_lease(
    monkeypatch: Any, tmp_path: Path
) -> None:
    configure_guard(
        monkeypatch,
        tmp_path,
        processes=[process(111, "owner-a", "powershell run.ps1")],
    )
    write_lease(tmp_path, pid=111, start_id="owner-a", worktree=str(tmp_path))
    monkeypatch.setattr(guard, "git_repo_id", lambda worktree: "local:agent-friend")
    monkeypatch.setattr(guard, "git_branch", lambda worktree: "feature/test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(GUARD_PATH),
            "release",
            "--worktree",
            str(tmp_path),
            "--mode",
            "tauri",
            "--ports",
            "1420,18800",
            "--owner-pid",
            "111",
        ],
    )

    assert guard.main() == 0
    assert not (tmp_path / "lease.json").exists()


def test_posix_entry_no_longer_contains_startup_dev_port_killer() -> None:
    run_sh = (ROOT / "scripts" / "dev" / "run.sh").read_text(encoding="utf-8")

    assert "cleanup_port" not in run_sh
    assert "尝试清理上次残留的 vite/tauri 进程" not in run_sh
