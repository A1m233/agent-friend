"""Run package writes outside desktop-app path virtualization, without elevation."""

from __future__ import annotations

import codecs
import ctypes
import io
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from paths import resolve_package_roots
from windows_shell_paths import SmokePathRedirection, verify_smoke_environment

ROOT = Path(__file__).resolve().parents[2]
STEPS = {
    "runtime",
    "runtime-verify",
    "installer",
    "all",
    "installer-smoke-build",
    "installer-smoke",
    "installer-smoke-verify",
}
MARKER = "AGENT_FRIEND_PACKAGE_NORMAL_HOST"


def _emit(text: str) -> None:
    # Preserve the UTF-8 log on disk; a legacy console must not abort its producer.
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(encoding, errors="backslashreplace").decode(encoding), end="", flush=True)


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def _roots(args: list[str]):
    options = {}
    for index, arg in enumerate(args):
        for option in ("state-dir", "releases-dir"):
            if arg == f"--{option}":
                options[option.replace("-", "_")] = Path(args[index + 1])
            elif arg.startswith(f"--{option}="):
                options[option.replace("-", "_")] = Path(arg.split("=", 1)[1])
    return resolve_package_roots(ROOT, **options)


def maybe_relay(args: list[str]) -> int | None:
    step = args[0] if args else "all"
    if sys.platform != "win32" or step not in STEPS or "--help" in args:
        return None
    try:
        verify_smoke_environment()
    except SmokePathRedirection as error:
        if os.environ.get(MARKER):
            raise RuntimeError(
                "Explorer package host is still redirected; refusing another launch"
            ) from error
        return _relay(args)
    return None


def _relay(args: list[str]) -> int:
    roots = _roots(args)
    directory = roots.work_root / "host-runs" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    # Resolve after creation so both processes address the same physical receipt directory.
    directory = directory.resolve()
    request = directory / "request.json"
    token = uuid.uuid4().hex
    _write_json(
        request,
        {
            "token": token,
            "args": args or ["all"],
            "state": str(roots.state_root),
            "releases": str(roots.releases_root),
        },
    )
    _emit(f"package_host: switching to Explorer; logs and result: {directory}\n")
    launcher = Path(__file__).with_suffix(".ps1")
    subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(launcher),
            "-Python",
            sys.executable,
            "-Worker",
            str(Path(__file__).resolve()),
            "-Request",
            str(request),
        ],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return _follow(directory, token)


def _follow(directory: Path, token: str) -> int:
    offset = 0
    decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("replace"), True)
    started = time.monotonic()
    status = directory / "started.json"
    output = directory / "output.log"
    result = directory / "result.json"

    def read_output() -> None:
        nonlocal offset
        if output.exists():
            with output.open("rb") as stream:
                stream.seek(offset)
                content = stream.read()
                offset = stream.tell()
            if content:
                _emit(decoder.decode(content))

    try:
        while True:
            read_output()
            if result.exists():
                value = json.loads(result.read_text(encoding="utf-8"))
                if value.get("token") != token or type(value.get("exitCode")) is not int:
                    raise RuntimeError("Package host returned an invalid completion receipt")
                read_output()
                _emit(decoder.decode(b"", final=True))
                return value["exitCode"]
            if status.exists():
                value = json.loads(status.read_text(encoding="utf-8"))
                if value.get("token") != token or type(value.get("pid")) is not int:
                    raise RuntimeError("Package host returned an invalid startup receipt")
                if not _alive(value["pid"]):
                    # Check completion again after the process-state observation.
                    if result.exists():
                        continue
                    raise RuntimeError(
                        f"Package host exited without completion; inspect {directory}; do not retry"
                    )
            if not status.exists() and time.monotonic() - started > 60:
                raise RuntimeError(
                    f"Package host startup outcome unknown; inspect {directory}; do not retry"
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        _emit(f"Package host may still be running; inspect {directory} before resuming\n")
        raise


def _alive(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE, no mutation rights.
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(handle)


def worker(request: Path) -> int:
    value = json.loads(request.read_text(encoding="utf-8"))
    args = value["args"]
    if (
        not isinstance(args, list)
        or not args
        or args[0] not in STEPS
        or not all(isinstance(x, str) for x in args)
    ):
        raise ValueError("Invalid package host arguments")
    directory = request.parent
    token = value["token"]
    _write_json(directory / "started.json", {"token": token, "pid": os.getpid()})
    code = 1
    with (directory / "output.log").open("wb") as output:
        try:
            verify_smoke_environment()
            env = dict(
                os.environ, **{MARKER: "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            )
            # Carry only explicit package roots, not credentials or the packaged parent's environment.
            env["AGENT_FRIEND_PACKAGE_STATE_DIR"] = value["state"]
            env["AGENT_FRIEND_RELEASES_DIR"] = value["releases"]
            code = subprocess.call(
                [sys.executable, "-u", "-B", str(Path(__file__).with_name("dispatch.py")), *args],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            output.write(traceback.format_exc().encode("utf-8"))
        finally:
            output.flush()
            _write_json(directory / "result.json", {"token": token, "exitCode": code})
    return code


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Use scripts/package/run.ps1 or run.sh")
    raise SystemExit(worker(Path(sys.argv[2])))
