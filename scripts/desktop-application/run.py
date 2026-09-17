"""Build and exercise the native desktop launcher without touching user applications."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def build_paths() -> tuple[Path, Path]:
    path = ROOT / "scripts/package/paths.py"
    spec = importlib.util.spec_from_file_location("desktop_package_paths", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    roots = module.resolve_package_roots(ROOT)
    paths = module.create_build_paths(roots, build_id="desktop-application-dev")
    return paths.cargo_target_dir, paths.build_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "build", "test", "smoke", "e2e"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--real-calls-authorized", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        print(json.dumps({"status": "unsupported", "platform": sys.platform}))
        return 0
    if args.action == "e2e":
        if not args.config or not args.real_calls_authorized:
            parser.error("e2e requires --config and explicit --real-calls-authorized")
        return subprocess.run(
            [
                "node",
                str(Path(__file__).with_name("e2e.mjs")),
                str(args.config.resolve()),
                "--real-calls-authorized",
            ],
            cwd=ROOT,
            check=False,
        ).returncode
    target, work = build_paths()
    command = [
        "cargo",
        "test" if args.action == "test" else "build",
        "--manifest-path",
        str(ROOT / "frontend/src-tauri/Cargo.toml"),
        "--target-dir",
        str(target),
        "--bin",
        "agent-friend-app-launcher",
    ]
    subprocess.run(
        command, cwd=ROOT, check=True, stdout=sys.stderr if args.action == "ensure" else None
    )
    executable = target / "debug/agent-friend-app-launcher.exe"
    if args.action == "test":
        return 0
    identity = json.loads(subprocess.check_output([str(executable), "--identity"]))
    manifest = {
        "schema_version": 1,
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "identity": identity,
    }
    executable.with_suffix(".identity.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    if args.action == "ensure":
        print(executable, flush=True)
    else:
        print(json.dumps({"executable": str(executable), "identity": identity}), flush=True)
    if args.action == "smoke":
        smoke(executable, work)
    return 0


def smoke(executable: Path, work: Path) -> None:
    """Put only the launcher in a kill-on-close Job; ask Explorer to start our fixture."""
    from ctypes import wintypes

    directory = work / f"smoke-{uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    fixture = directory / "desktop-fixture.exe"
    subprocess.run(
        ["rustc", str(Path(__file__).with_name("fixture.rs")), "-o", str(fixture)],
        check=True,
        cwd=ROOT,
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_ws", ctypes.c_size_t),
            ("max_ws", ctypes.c_size_t),
            ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(f"counter_{i}", ctypes.c_uint64) for i in range(6)]

    class Limits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", IoCounters),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process = None
    target_process = None
    try:
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if not kernel.AssignProcessToJobObject(job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        arguments = [str(directory), "", "space value", 'quote"value', "trailing\\", "&literal"]
        stdout, stderr = process.communicate(
            json.dumps(
                {
                    "protocol_version": 1,
                    "executable_path": str(fixture),
                    "arguments": arguments,
                    "working_directory": str(directory),
                }
            ).encode(),
            timeout=15,
        )
        records = [json.loads(line) for line in stdout.splitlines()]
        if process.returncode != 0 or records[-1].get("status") != "accepted":
            raise RuntimeError(f"launch failed: {records}; {stderr.decode(errors='replace')}")
        marker = directory / "started.json"
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        observed = json.loads(marker.read_text(encoding="utf-8"))
        if observed["arguments"] != arguments[1:]:
            raise RuntimeError(f"argument roundtrip failed: {observed}")
        target_process = kernel.OpenProcess(0x100000 | 0x1000, False, observed["pid"])
        in_job = wintypes.BOOL()
        if not target_process or not kernel.IsProcessInJob(
            target_process, job, ctypes.byref(in_job)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        assert not in_job.value, "desktop application inherited launcher Job"
        kernel.CloseHandle(job)
        job = None
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        assert kernel.WaitForSingleObject(target_process, 1200) == 258, (
            "target died after Job close"
        )
        report = {
            "status": "passed",
            "target_outside_launcher_job": True,
            "survived_helper_exit_and_job_close": True,
            "argument_roundtrip": True,
            "helper_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "scope": "native fixture; not MuMu or installed product",
        }
        (directory / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"report": str(directory / "report.json"), **report}), flush=True)
    finally:
        (directory / "stop").touch()
        if target_process:
            kernel.CloseHandle(target_process)
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if job:
            kernel.CloseHandle(job)


if __name__ == "__main__":
    raise SystemExit(main())
