"""Developer entrypoint for the pinned Codex command runtime payload."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "package"))

from vendor.codex import load_lock, prepare, verify_prepared  # noqa: E402


def smoke(executable: Path) -> dict[str, object]:
    from agent.command_runtime import CommandRuntime
    from agent.command_runtime.runtime import sandbox_context

    with tempfile.TemporaryDirectory(prefix="agent-friend-command-runtime-") as temp_value:
        root = Path(temp_value).resolve()
        runtime = CommandRuntime(
            executable,
            codex_home=_product_codex_home(),
            execution_root=_product_execution_root(),
        )
        writable = sandbox_context(
            workdir=root,
            writable=True,
            additional_roots=(),
            network_enabled=False,
        )
        try:
            buffered = _start(runtime, root, writable, "smoke-buffered", "echo ok")
            buffered = runtime.wait(
                buffered.process_id,
                session_id="smoke",
                run_id="smoke-buffered",
                timeout_seconds=30,
            )
            if buffered.exit_code != 0 or b"ok" not in buffered.stdout_tail:
                raise RuntimeError(f"buffered command failed: {buffered.to_dict()}")

            streaming = runtime.start_process(
                argv=(
                    _system_cmd(),
                    "/D",
                    "/Q",
                ),
                workdir=root,
                env=_command_env(),
                mode="streaming",
                session_id="smoke",
                run_id="smoke-streaming",
                tool_call_id="smoke-streaming",
                sandbox=writable,
            )
            runtime.write(
                streaming.process_id,
                b"echo input:hello\r\nexit\r\n",
                session_id="smoke",
                run_id="smoke-streaming",
            )
            streaming = runtime.wait(
                streaming.process_id,
                session_id="smoke",
                run_id="smoke-streaming",
                timeout_seconds=30,
            )
            if streaming.exit_code != 0 or b"input:hello" not in streaming.pty_tail:
                raise RuntimeError(f"streaming command failed: {streaming.to_dict()}")

            cancelled = _start(runtime, root, writable, "smoke-cancel", "timeout /t 30 >nul")
            cancelled = runtime.terminate(
                cancelled.process_id,
                session_id="smoke",
                run_id="smoke-cancel",
            )
            if cancelled.status != "cancelled":
                raise RuntimeError("cancellation did not become terminal")

            denied = _start(
                runtime,
                root,
                sandbox_context(
                    workdir=root,
                    writable=False,
                    additional_roots=(),
                    network_enabled=False,
                ),
                "smoke-denied",
                "echo no>denied.txt",
            )
            denied = runtime.wait(
                denied.process_id,
                session_id="smoke",
                run_id="smoke-denied",
                timeout_seconds=30,
            )
            if denied.exit_code == 0 or (root / "denied.txt").exists():
                raise RuntimeError("read-only sandbox allowed a write")
            skill_fingerprint = _smoke_builtin_skill_tool(runtime, root)
            return {
                "status": "ok",
                "sandbox_type": buffered.sandbox_type,
                "buffered": True,
                "streaming": True,
                "cancelled": True,
                "write_denied": True,
                "skill_tool_path": True,
                "skill_snapshot_fingerprint": skill_fingerprint,
            }
        finally:
            runtime.close()


def _smoke_builtin_skill_tool(runtime, root: Path) -> str:
    from agent.approvals import ApprovalBroker
    from agent.command_runtime import CommandApprovalDecision, ExecCommandTool
    from agent.command_runtime.tools import (
        COMMAND_RUN_CONTEXT_ARG,
        COMMAND_RUN_ID_ARG,
        COMMAND_SESSION_ID_ARG,
        COMMAND_SKILL_SNAPSHOT_ARG,
        COMMAND_SURFACE_ARG,
        COMMAND_TOOL_CALL_ID_ARG,
    )
    from agent.skills import SkillCatalogService, SkillSelection
    from agent.tool_runtime import ToolRunContext

    service = SkillCatalogService(
        personal_root=root / "skill-catalog" / "personal",
        shared_root=root / "skill-catalog" / "shared",
        watcher_enabled=False,
    )
    catalog = service.initialize()
    record = next(
        (item for item in catalog.entries if item.id == "builtin/skill-creator"),
        None,
    )
    if record is None:
        raise RuntimeError("builtin skill-creator was not discovered")
    turn_snapshot = service.begin_turn(
        (SkillSelection(id=record.id, expected_version=record.version),)
    )
    if len(turn_snapshot.activations) != 1 or turn_snapshot.activation_issues:
        raise RuntimeError("builtin skill-creator could not be activated")

    target = root / "skill-validation-target"
    target.mkdir()
    (target / "SKILL.md").write_text(
        "---\nname: smoke-target\ndescription: Validate the governed Skill Tool path.\n---\n",
        encoding="utf-8",
    )
    entry = record.package_root / "scripts" / "quick_validate.py"
    base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    source_site_packages = Path(sys.executable).resolve().parents[1] / "Lib" / "site-packages"
    smoke_dependencies = root / "skill-python-dependencies"
    shutil.copytree(source_site_packages / "yaml", smoke_dependencies / "yaml")
    command = f"set PYTHONPATH={smoke_dependencies}&&" + subprocess.list2cmdline(
        [str(base_executable), str(entry), str(target)]
    )
    interpreter_roots = {base_executable.parent, smoke_dependencies}
    broker = ApprovalBroker()
    context = ToolRunContext.create("")
    result_box: list[Any] = []
    args = {
        "cmd": command,
        "workdir": str(record.package_root),
        "skill": {"id": record.id, "entry": "scripts/quick_validate.py"},
        "access": {
            "filesystem": "read_only",
            "additional_roots": [
                {"path": str(path), "access": "read_only"}
                for path in (target, *sorted(interpreter_roots, key=str))
            ],
            "network": "disabled",
        },
        "reason": "verify builtin Skill execution through the governed Tool path",
        COMMAND_SESSION_ID_ARG: "smoke",
        COMMAND_RUN_ID_ARG: "smoke-skill",
        COMMAND_TOOL_CALL_ID_ARG: "smoke-skill",
        COMMAND_SURFACE_ARG: "desktop_chat",
        COMMAND_RUN_CONTEXT_ARG: context,
        COMMAND_SKILL_SNAPSHOT_ARG: turn_snapshot,
    }
    tool = ExecCommandTool(runtime, broker)
    thread = threading.Thread(target=lambda: result_box.append(tool.invoke(args)), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        pending = ()
        while time.monotonic() < deadline:
            pending = broker.pending(kind="command")
            if pending:
                break
            time.sleep(0.01)
        if not pending:
            raise RuntimeError("builtin Skill command did not request approval")
        request = pending[0]
        if request.details.skill is None:
            raise RuntimeError("builtin Skill command lost its structured binding")
        if not broker.decide(
            request.request_id,
            CommandApprovalDecision(allow=True),
            session_id="smoke",
            run_id="smoke-skill",
            expected_kind="command",
        ):
            raise RuntimeError("builtin Skill command approval was rejected")
        thread.join(timeout=40)
        if thread.is_alive() or not result_box:
            raise RuntimeError("builtin Skill command did not finish")
        result = result_box[0]
        payload = json.loads(result.text)
        if (
            payload.get("status") != "exited"
            or payload.get("exit_code") != 0
            or "Skill is valid!" not in str(payload.get("stdout", ""))
        ):
            raise RuntimeError(
                "builtin Skill command failed: " + json.dumps(payload, ensure_ascii=False)
            )
        return request.details.skill.fingerprint
    finally:
        context.close_commands()


def _start(runtime, root: Path, sandbox, run_id: str, command: str):
    return runtime.start_process(
        argv=(
            _system_cmd(),
            "/D",
            "/S",
            "/C",
            command,
        ),
        workdir=root,
        env=_command_env(),
        mode="buffered",
        session_id="smoke",
        run_id=run_id,
        tool_call_id=run_id,
        sandbox=sandbox,
    )


def _system_cmd() -> str:
    system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
    if not system_root:
        raise RuntimeError("Windows system root is unavailable")
    executable = Path(system_root) / "System32" / "cmd.exe"
    if not executable.is_file():
        raise RuntimeError("System cmd.exe is unavailable")
    return str(executable)


def _command_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "PATHEXT",
            "SystemRoot",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "APPDATA",
            "LOCALAPPDATA",
        }
    }


def _product_codex_home() -> Path:
    from agent.paths import sessions_dir

    return sessions_dir().parent / "command-runtime" / "codex-home"


def _product_execution_root() -> Path:
    return Path(tempfile.gettempdir()) / "agent-friend-command-execution"


def _sandbox_ready() -> bool:
    home = _product_codex_home()
    return (home / ".sandbox" / "setup_marker.json").is_file() and (
        home / ".sandbox-secrets" / "sandbox_users.json"
    ).is_file()


def setup_windows(executable: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows sandbox setup is only available on Windows")
    home = _product_codex_home()
    home.mkdir(parents=True, exist_ok=True)
    from agent.command_runtime import CommandRuntime

    runtime = CommandRuntime(
        executable,
        codex_home=home,
        execution_root=_product_execution_root(),
    )
    runtime.setup_windows_sandbox()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "doctor", "path", "status", "setup", "smoke"))
    args = parser.parse_args()
    executable = prepare(ROOT) if args.action == "prepare" else verify_prepared(ROOT)
    if args.action == "path":
        print(executable)
        return 0
    if args.action == "status":
        print(
            json.dumps(
                {
                    "status": "ready" if _sandbox_ready() else "setup_required",
                    "codex_home": str(_product_codex_home()),
                    "execution_root": str(_product_execution_root()),
                }
            )
        )
        return 0 if _sandbox_ready() else 2
    if args.action == "setup":
        setup_windows(executable)
        print(
            json.dumps(
                {
                    "status": "ready",
                    "codex_home": str(_product_codex_home()),
                    "execution_root": str(_product_execution_root()),
                }
            )
        )
        return 0
    if args.action == "smoke":
        print(json.dumps(smoke(executable), ensure_ascii=False))
        return 0
    if args.action == "doctor":
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Codex identity check failed: {completed.stderr.strip()}")
        expected = load_lock()["version"]
        if expected not in completed.stdout:
            raise RuntimeError(
                f"Codex version mismatch: expected {expected}, got {completed.stdout.strip()}"
            )
        print(json.dumps({"status": "ok", "path": str(executable), "version": expected}))
    else:
        print(executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
