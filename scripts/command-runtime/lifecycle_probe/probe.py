"""Real pinned exec-server probes without sandbox provisioning, network or model calls.

Every native descendant is contained in a probe-owned Windows Job. A completed report
is evidence about host processes and RPC only, never a passed sandbox qualification.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import types
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CLIENT = ROOT / "agent/src/agent/command_runtime/exec_server.py"
WAIT_MS = 3000
CASES = (
    "serial-control",
    "concurrent-control",
    "concurrent-query-saturation",
    "server-loss",
    "closed-with-descendant",
)
UNVERIFIED = [
    "windows_sandbox_preparation_cancellation",
    "alternate_identity_runner_containment",
    "elevated_helper_containment",
    "first_native_error_before_identity_refresh",
    "cross_process_identity_maintenance",
    "windows_10_native_behavior",
    "installed_and_frozen_runtime",
]


def isolated_environment(root: Path) -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "CODEX_HOME"):
        target = root / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        env[name] = str(target)
    env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", NO_COLOR="1")
    return env


def load_client():
    package = types.ModuleType("af_m0_command_runtime")
    package.__path__ = [str(CLIENT.parent)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location("af_m0_command_runtime.exec_server", CLIENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ExecServerClient


def wait_marker(root: Path, name: str) -> dict:
    path = root / f"{name}.json"
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return json.loads(path.read_text(encoding="utf-8"))


def start_fixture(client, root: Path, mode: str) -> None:
    response = client.request(
        "process/start",
        {
            "processId": "proc_m0",
            "argv": [sys.executable, str(HERE / "fixture.py"), mode, str(root)],
            "cwd": root.as_uri(),
            "env": isolated_environment(root),
            "tty": False,
            "pipeStdin": False,
            "arg0": None,
        },
        timeout_seconds=10,
    )
    if response.get("sandboxType") != "none":
        raise RuntimeError("probe_requires_explicit_unsandboxed_response")


def read_process(client, *, wait_ms: int = 0):
    return client.request(
        "process/read",
        {
            "processId": "proc_m0",
            "afterSeq": 0,
            "maxBytes": 1024,
            "waitMs": wait_ms,
        },
        timeout_seconds=6,
    )


def probe_control(client, *, saturated: bool) -> dict:
    poll_count = 2 if saturated else 1
    submitted = threading.Condition()
    submission_count = 0
    original_write = client._write

    def record_submission(message):
        nonlocal submission_count
        original_write(message)
        if message.get("method") == "process/read":
            with submitted:
                submission_count += 1
                submitted.notify_all()

    client._write = record_submission
    polls = [{} for _ in range(poll_count)]

    def poll_into(result, wait_ms):
        try:
            result["response"] = read_process(client, wait_ms=wait_ms)
        except Exception as error:
            result["error"] = type(error).__name__

    threads = [
        threading.Thread(target=poll_into, args=(poll, WAIT_MS), daemon=True) for poll in polls
    ]
    for thread in threads:
        thread.start()
    with submitted:
        if not submitted.wait_for(lambda: submission_count == poll_count, timeout=2):
            raise RuntimeError("long_poll_not_submitted")
    time.sleep(0.15)
    query = {}
    pending_query = None
    if saturated:
        thread = threading.Thread(target=poll_into, args=(query, 0), daemon=True)
        threads.append(thread)
        thread.start()
        with submitted:
            if not submitted.wait_for(lambda: submission_count == poll_count + 1, timeout=2):
                raise RuntimeError("snapshot_query_not_submitted")
        time.sleep(0.15)
        pending_query = thread.is_alive()
    started = time.perf_counter()
    response = client.request("process/terminate", {"processId": "proc_m0"}, timeout_seconds=6)
    elapsed = time.perf_counter() - started
    for thread in threads:
        thread.join(timeout=7)
    if any(thread.is_alive() for thread in threads) or any(
        "error" in poll for poll in [*polls, query]
    ):
        raise RuntimeError("process_query_failed")
    return {
        "long_poll_ms": WAIT_MS,
        "long_poll_count": poll_count,
        "terminate_elapsed_ms": round(elapsed * 1000, 3),
        "terminate_response": response,
        "long_poll_responses": [poll["response"] for poll in polls],
        "snapshot_pending_before_terminate": pending_query,
        "snapshot_response": query.get("response"),
        "control_delayed_by_long_poll": elapsed >= WAIT_MS / 1000 * 0.7,
        "fixture_confirmed_in_probe_job": True,
    }


def worker(case: str, executable: Path, root: Path, job_name: str) -> dict:
    from windows_job import ObservedProcess, join_before_spawning

    join_before_spawning(job_name)
    client_type = load_client()
    command = [str(executable), "exec-server", "--strict-config", "--listen", "stdio"]
    if case.startswith("concurrent-"):
        command.extend(["--concurrent-requests", "2"])
    notifications = []
    client = client_type(
        command,
        env=isolated_environment(root),
        notification=lambda method, params: notifications.append(method),
    )
    observed = None
    try:
        client.start()
        mode = "root-with-child" if case == "closed-with-descendant" else "quiet"
        start_fixture(client, root, mode)
        marker = wait_marker(root, "child" if mode == "root-with-child" else "quiet")
        observed = ObservedProcess(marker["pid"], job_name)
        if case.endswith("control") or case == "concurrent-query-saturation":
            return probe_control(client, saturated=case == "concurrent-query-saturation")
        if case == "closed-with-descendant":
            deadline = time.monotonic() + 8
            result = read_process(client)
            while not result["closed"] and time.monotonic() < deadline:
                time.sleep(0.03)
                result = read_process(client)
            if not result["closed"]:
                raise RuntimeError("root_did_not_close")
            return {
                "query_response": result,
                "descendant_alive_after_closed": observed.alive(),
                "fixture_confirmed_in_probe_job": True,
            }
        if case == "server-loss":
            process = client._process
            assert process is not None
            process.kill()  # Exact probe-owned Popen handle, never a saved/external PID.
            process.wait(timeout=5)
            time.sleep(0.3)
            return {
                "executor_exit_confirmed": process.poll() is not None,
                "fixture_alive_after_executor_exit": observed.alive(),
                "connection_lost_observed": "connection/lost" in notifications,
                "fixture_confirmed_in_probe_job": True,
            }
        raise ValueError("unknown probe case")
    finally:
        (root / "stop").touch()
        if observed is not None:
            observed.close()
        client.close()


def run_case(case: str, executable: Path, root: Path) -> dict:
    from .windows_job import ProbeJob

    job_name = f"Local\\AF-M0-{uuid4().hex}"
    job = ProbeJob(job_name)
    process = None
    result = {"case": case, "status": "failed"}
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__)),
                "--worker",
                case,
                str(executable),
                str(root),
                job_name,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=isolated_environment(root),
            cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout, stderr = process.communicate(timeout=25)
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")
            for path in (
                str(root),
                str(executable.parent),
                str(ROOT),
                str(Path(sys.executable).parent),
            ):
                detail = detail.replace(path, "<probe-path>")
            result["failure"] = detail[-4000:]
        else:
            result.update(status="observed", observation=json.loads(stdout))
    except subprocess.TimeoutExpired:
        result["failure"] = "probe_worker_timeout"
    finally:
        try:
            result["remaining_job_processes"] = job.terminate_and_wait()
            if process is not None:
                process.communicate(timeout=5)
        finally:
            job.close()
            if process is not None and process.poll() is None:
                process.kill()  # Covers a worker that failed before joining the Job.
                process.communicate(timeout=5)
    return result


def source_identity(archive: Path) -> dict:
    paths = [
        CLIENT,
        *sorted((CLIENT.parent / "lifecycle").glob("*.py")),
        *sorted(HERE.glob("*.py")),
        HERE.parent / "run.py",
        ROOT / "scripts/package/vendor/codex.lock.json",
    ]
    snapshots = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in paths}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in snapshots.items():
            bundle.writestr(name, data)
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "overlay_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in snapshots.items()
        },
        "archive": archive.name,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


def run(executable: Path, report: Path) -> dict:
    report = report.resolve()
    archive = report.with_suffix(".sources.zip")
    if report.exists() or archive.exists():
        raise ValueError("report_exists: use a new path to preserve earlier observations")
    report.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        result = {"schema_version": 1, "status": "not_applicable", "platform": platform.system()}
    else:
        identity = json.loads(
            (executable.parent.parent / "identity.json").read_text(encoding="utf-8")
        )
        result = {
            "schema_version": 1,
            "status": "completed",
            "qualification": "not_established",
            "scope": "host_process_and_rpc_only; no sandbox provisioning or elevation",
            "recorded_at": datetime.now(UTC).isoformat(),
            "platform": {
                "system": platform.system(),
                "version": platform.version(),
                "architecture": platform.machine(),
            },
            "source": source_identity(archive),
            "backend": {
                key: identity[key]
                for key in ("version", "release_tag", "archive_sha256", "executable_sha256")
            },
            "unverified": UNVERIFIED,
            "cases": [],
        }
        for case in CASES:
            with tempfile.TemporaryDirectory(prefix="af-m0-") as temporary:
                result["cases"].append(run_case(case, executable, Path(temporary).resolve()))
        if any(case["status"] != "observed" for case in result["cases"]):
            result["status"] = "failed"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 6 or sys.argv[1] != "--worker":
        raise SystemExit("Use scripts/command-runtime/run.* lifecycle-probe --report NEW_FILE")
    print(json.dumps(worker(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])))
