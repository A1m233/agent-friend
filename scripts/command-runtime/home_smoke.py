"""Windows host-directory repair smoke with real execution, no identity provisioning."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from agent.command_runtime import CommandRuntime
from agent.command_runtime.runtime import sandbox_context
from agent.command_runtime.windows_home import (
    elevated_command,
    home_directories,
    literal,
    prepare_home,
    repair_home_script,
)
from agent.command_runtime.windows_state import compatible_state, read_state


def run(executable: Path, home: Path, report: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("home-smoke requires Windows")
    # This smoke is intentionally recovery-only: never rotate the shared Windows
    # sandbox accounts just to validate a new isolated data directory.
    marker = home / ".sandbox/setup_marker.json"
    users = home / ".sandbox-secrets/sandbox_users.json"
    if not compatible_state(read_state(marker), read_state(users)):
        raise RuntimeError("home-smoke requires an existing compatible sandbox identity")
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (marker, users)]
    report.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"status": "running", "commands": [], "provisioning_attempted": False}

    def forbidden_setup(*_args: object, **_kwargs: object) -> None:
        result["provisioning_attempted"] = True
        raise RuntimeError("home-smoke refuses identity reprovisioning")

    with tempfile.TemporaryDirectory(prefix="af-home-smoke-") as tmp:
        root = Path(tmp)
        runtime = CommandRuntime(
            executable,
            codex_home=home,
            managed_home_root=home.parent,
            execution_root=root / "execution",
            diagnostics_path=report.parent / "home-smoke-diagnostics.json",
        )
        runtime._perform_windows_sandbox_setup = forbidden_setup  # type: ignore[method-assign]
        try:
            # Exercise the same inheritance grant used immediately before real
            # provisioning, without creating/updating shared sandbox accounts.
            fresh = root / "fresh/runtime/codex-home"
            directories = home_directories(fresh, fresh.parent)
            prepare_home(directories)
            user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
            creation = repair_home_script(directories, user) + (
                f"[IO.File]::WriteAllText({literal(str(fresh / 'config.toml'))},"
                "'admin-created-host-readable',[Text.UTF8Encoding]::new($false))"
            )
            elevated = subprocess.run(
                elevated_command(creation, os.environ["SYSTEMROOT"]),
                env=runtime._windows_setup_env(),
                capture_output=True,
                timeout=90,
                check=False,
            )
            if elevated.returncode != 0:
                raise RuntimeError("elevated fixture creation failed")
            prepare_home(directories)
            result["new_home_admin_file_readable"] = (fresh / "config.toml").read_text(
                encoding="utf-8"
            ) == "admin-created-host-readable"
            if not result["new_home_admin_file_readable"]:
                raise RuntimeError("new host home failed inheritance check")
            runtime.setup_windows_sandbox()
            for writable in (False, True):
                workdir = root / ("writable" if writable else "read-only")
                workdir.mkdir()
                context = sandbox_context(
                    workdir=workdir,
                    writable=writable,
                    additional_roots=(),
                    network_enabled=False,
                )
                if not writable:
                    runtime.prepare_owned_read_only_workdir(workdir, context)
                process = runtime.start_process(
                    argv=(
                        str(Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe"),
                        "/D",
                        "/C",
                        "echo AF-HOME-REPAIR-OK",
                    ),
                    workdir=workdir,
                    env=dict(os.environ),
                    mode="buffered",
                    session_id="home-smoke",
                    run_id="home-smoke",
                    tool_call_id="home-smoke",
                    sandbox=context,
                )
                process = runtime.wait(
                    process.process_id,
                    session_id="home-smoke",
                    run_id="home-smoke",
                    timeout_seconds=30,
                )
                passed = (
                    process.exit_code == 0
                    and b"AF-HOME-REPAIR-OK" in process.stdout_tail
                    and bool(process.sandbox_type)
                    and process.sandbox_type != "none"
                )
                result["commands"].append(
                    {
                        "writable": writable,
                        "exit_code": process.exit_code,
                        "sandbox_type": process.sandbox_type,
                        "passed": passed,
                    }
                )
                if not passed:
                    raise RuntimeError("real sandbox command did not pass")
            after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (marker, users)]
            result["identity_unchanged"] = before == after
            if before != after:
                raise RuntimeError("sandbox identity changed during recovery-only smoke")
            result["status"] = "passed"
        except Exception as exc:
            result.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            runtime.close()
            result["incidents"] = runtime.diagnostics()["incidentEvidence"]
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
