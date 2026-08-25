"""Developer entrypoint for the pinned Codex command runtime payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "package"))

from vendor.codex import LOCK_PATH, load_lock, prepare  # noqa: E402

_CACHE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_CACHE_THREAD_LOCKS_GUARD = threading.Lock()
_CACHE_LOCK_TIMEOUT_SECONDS = 300.0


def _source_cache_root() -> Path:
    """Return the user-level cache used only for pinned source-mode payloads."""

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is required for the command runtime dev cache")
        return Path(local_app_data) / "agent-friend" / "dev-cache" / "command-runtime"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "agent-friend" / "command-runtime"
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    cache_home = Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"
    return cache_home / "agent-friend" / "command-runtime"


def _source_cache_base() -> Path:
    lock_identity = hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()
    return _source_cache_root() / "codex" / lock_identity


def _verify_source_runtime(repo_root: Path = ROOT) -> Path:
    # vendor.prepare verifies the complete identity before returning. Offline mode
    # turns it into a verify-only operation when the prepared payload is absent.
    return prepare(repo_root, cache_base=_source_cache_base(), offline=True)


def _try_acquire_platform_lock(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release_platform_lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_cache_lock(
    path: Path,
    *,
    timeout_seconds: float = _CACHE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Serialize prepares across threads and worktree processes."""

    key = str(path.resolve())
    with _CACHE_THREAD_LOCKS_GUARD:
        thread_lock = _CACHE_THREAD_LOCKS.setdefault(key, threading.Lock())
    if not thread_lock.acquire(timeout=timeout_seconds):
        raise RuntimeError(f"timed out waiting for command runtime cache lock: {path}")

    handle = None
    platform_lock_acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        deadline = time.monotonic() + timeout_seconds
        while not _try_acquire_platform_lock(handle):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"timed out waiting for command runtime cache lock: {path}")
            time.sleep(0.1)
        platform_lock_acquired = True
        yield
    finally:
        if handle is not None:
            if platform_lock_acquired:
                with suppress(OSError):
                    _release_platform_lock(handle)
            handle.close()
        thread_lock.release()


def ensure_source_runtime(repo_root: Path = ROOT) -> Path:
    """Return a verified pinned runtime, preparing it once when necessary."""

    try:
        return _verify_source_runtime(repo_root)
    except RuntimeError:
        pass

    cache_base = _source_cache_base()
    lock_path = cache_base.parent / f".{cache_base.name}.lock"
    with _exclusive_cache_lock(lock_path):
        try:
            return _verify_source_runtime(repo_root)
        except RuntimeError:
            return prepare(repo_root, cache_base=cache_base)


def _concise_error(error: Exception) -> str:
    detail = " ".join(str(error).splitlines()).strip() or "unknown error"
    return f"{type(error).__name__}: {detail}"


def smoke(executable: Path) -> dict[str, object]:
    from agent.command_runtime import CommandRuntime, ExecCommandTool, TerminateCommandTool
    from agent.command_runtime.runtime import sandbox_context
    from agent.command_runtime.tools import (
        COMMAND_RUN_CONTEXT_ARG,
        COMMAND_RUN_ID_ARG,
        COMMAND_SESSION_ID_ARG,
        COMMAND_TOOL_CALL_ID_ARG,
    )
    from agent.tool_runtime import ToolRunContext

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

            host_user = runtime.start_process(
                argv=(_system_cmd(), "/D", "/S", "/C", "echo host-user-ok"),
                workdir=root,
                env=dict(os.environ),
                mode="buffered",
                session_id="smoke",
                run_id="smoke-host-user",
                tool_call_id="smoke-host-user",
                sandbox=None,
            )
            host_user = runtime.wait(
                host_user.process_id,
                session_id="smoke",
                run_id="smoke-host-user",
                timeout_seconds=30,
            )
            if (
                host_user.exit_code != 0
                or host_user.sandbox_type != "none"
                or b"host-user-ok" not in host_user.stdout_tail
            ):
                raise RuntimeError(f"host-user command failed: {host_user.to_dict()}")

            powershell_argv = ExecCommandTool(
                runtime,
                None,
                base_env=dict(os.environ),
            )._shell_argv(
                'pwsh.exe -NoProfile -Command "Write-Output agent-friend-powershell-ok"',
                "buffered",
            )
            powershell = runtime.start_process(
                argv=powershell_argv,
                workdir=root,
                env=_command_env(),
                mode="buffered",
                session_id="smoke",
                run_id="smoke-powershell-argv",
                tool_call_id="smoke-powershell-argv",
                sandbox=writable,
            )
            powershell = runtime.wait(
                powershell.process_id,
                session_id="smoke",
                run_id="smoke-powershell-argv",
                timeout_seconds=30,
            )
            if (
                powershell.exit_code != 0
                or b"agent-friend-powershell-ok" not in powershell.stdout_tail
            ):
                raise RuntimeError(f"direct PowerShell argv command failed: {powershell.to_dict()}")

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

            cancelled = runtime.start_process(
                argv=(_system_cmd(), "/D", "/Q"),
                workdir=root,
                env=_command_env(),
                mode="streaming",
                session_id="smoke",
                run_id="smoke-cancel",
                tool_call_id="smoke-cancel",
                sandbox=writable,
            )
            cancel_context = ToolRunContext.create("")
            cancel_context.register_command_handle(
                cancelled.process_id,
                lambda reason: runtime.terminate(
                    cancelled.process_id,
                    session_id="smoke",
                    run_id="smoke-cancel",
                    reason=reason,
                    wait_for_terminal_seconds=0,
                ),
            )
            termination = TerminateCommandTool(runtime).invoke(
                {
                    "process_id": cancelled.process_id,
                    COMMAND_SESSION_ID_ARG: "smoke",
                    COMMAND_RUN_ID_ARG: "smoke-cancel",
                    COMMAND_TOOL_CALL_ID_ARG: "smoke-terminate",
                    COMMAND_RUN_CONTEXT_ARG: cancel_context,
                }
            )
            termination_payload = json.loads(termination.text)
            cancel_context.close_commands()
            if (
                termination.is_error
                or termination_payload.get("status") != "cancelled"
                or termination_payload.get("terminal_reason") != "agent_requested"
            ):
                raise RuntimeError(f"Agent termination Tool failed: {termination.text}")

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
                "host_user": True,
                "powershell_argv": True,
                "streaming": True,
                "cancelled": True,
                "agent_termination_tool": True,
                "write_denied": True,
                "skill_tool_path": True,
                "skill_snapshot_fingerprint": skill_fingerprint,
            }
        finally:
            runtime.close()


def network_smoke(executable: Path) -> dict[str, object]:
    """Verify approved HTTPS through the public Tool and exec-server sandbox."""
    from agent.approvals import ApprovalBroker
    from agent.command_runtime import CommandApprovalDecision, CommandRuntime, ExecCommandTool
    from agent.command_runtime.tools import (
        COMMAND_RUN_CONTEXT_ARG,
        COMMAND_RUN_ID_ARG,
        COMMAND_SESSION_ID_ARG,
        COMMAND_SURFACE_ARG,
        COMMAND_TOOL_CALL_ID_ARG,
    )
    from agent.tool_runtime import ToolRunContext

    with tempfile.TemporaryDirectory(prefix="agent-friend-command-network-") as temp_value:
        root = Path(temp_value).resolve()
        probe = root / "network_probe.py"
        probe.write_text(
            """import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request

result = {
    "environment": {
        name: bool(os.environ.get(name))
        for name in ("SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP")
    }
}
try:
    socket.socket().close()
    result["winsock"] = "ok"
except OSError as exc:
    result.update(stage="winsock", error=repr(exc))
    print(json.dumps(result))
    raise SystemExit(11)
try:
    with urllib.request.urlopen(
        "https://api.github.com/zen",
        timeout=20,
    ) as response:
        body = response.read(4096)
        result.update(stage="https", http_status=response.status, response_bytes=len(body))
except urllib.error.URLError as exc:
    reason = exc.reason
    if isinstance(reason, socket.gaierror):
        stage = "dns"
    elif isinstance(reason, ssl.SSLError):
        stage = "tls"
    elif isinstance(reason, OSError) and getattr(reason, "winerror", None) == 10106:
        stage = "winsock"
    else:
        stage = "connect"
    result.update(stage=stage, error=repr(reason))
    print(json.dumps(result))
    raise SystemExit(12)
print(json.dumps(result))
""",
            encoding="utf-8",
        )
        runtime = CommandRuntime(
            executable,
            codex_home=_product_codex_home(),
            execution_root=_product_execution_root(),
        )
        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        broker = ApprovalBroker()
        context = ToolRunContext.create("")
        result_box: list[Any] = []
        args = {
            "cmd": subprocess.list2cmdline([str(base_executable), str(probe)]),
            "workdir": str(root),
            "access": {
                "filesystem": "read_only",
                "additional_roots": [{"path": str(base_executable.parent), "access": "read_only"}],
                "network": "enabled",
            },
            "reason": "verify real HTTPS through the governed command Tool",
            COMMAND_SESSION_ID_ARG: "smoke",
            COMMAND_RUN_ID_ARG: "smoke-network",
            COMMAND_TOOL_CALL_ID_ARG: "smoke-network",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: context,
        }
        thread = threading.Thread(
            target=lambda: result_box.append(ExecCommandTool(runtime, broker).invoke(args)),
            daemon=True,
        )
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
                raise RuntimeError("network command did not request approval")
            request = pending[0]
            if not broker.decide(
                request.request_id,
                CommandApprovalDecision(allow=True),
                session_id="smoke",
                run_id="smoke-network",
                expected_kind="command",
            ):
                raise RuntimeError("network command approval was rejected")
            thread.join(timeout=40)
            if thread.is_alive() or not result_box:
                raise RuntimeError("network command did not finish")
            payload = json.loads(result_box[0].text)
            if payload.get("status") != "exited" or payload.get("exit_code") != 0:
                raise RuntimeError(
                    "managed network command failed: " + json.dumps(payload, ensure_ascii=False)
                )
            lines = [line for line in str(payload.get("stdout", "")).splitlines() if line]
            probe_result = json.loads(lines[-1]) if lines else {}
            if probe_result.get("http_status") != 200 or probe_result.get("winsock") != "ok":
                raise RuntimeError(
                    "managed network probe was incomplete: "
                    + json.dumps(probe_result, ensure_ascii=False)
                )
            return {
                "status": "ok",
                "sandbox_type": payload.get("sandbox_type"),
                "https": True,
                "probe": probe_result,
            }
        finally:
            context.close_commands()
            runtime.close()


def skill_install_smoke(executable: Path) -> dict[str, object]:
    """Install one public Skill through the governed Tool into an isolated catalog."""
    from agent.approvals import ApprovalBroker
    from agent.command_runtime import CommandApprovalDecision, CommandRuntime, ExecCommandTool
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

    with tempfile.TemporaryDirectory(prefix="agent-friend-skill-install-") as temp_value:
        root = Path(temp_value).resolve()
        personal_root = root / "personal-skills"
        personal_root.mkdir(parents=True)
        _ensure_windows_owner_inheritance(personal_root)
        service = SkillCatalogService(
            personal_root=personal_root,
            shared_root=root / "shared-skills",
            watcher_enabled=True,
        )
        catalog = service.initialize()
        installer = next(
            (record for record in catalog.entries if record.id == "builtin/skill-installer"),
            None,
        )
        if installer is None:
            raise RuntimeError("builtin skill-installer was not discovered")
        turn_snapshot = service.begin_turn(
            (SkillSelection(id=installer.id, expected_version=installer.version),)
        )
        if len(turn_snapshot.activations) != 1 or turn_snapshot.activation_issues:
            raise RuntimeError("builtin skill-installer could not be activated")
        service.start()

        runtime = CommandRuntime(
            executable,
            codex_home=_product_codex_home(),
            execution_root=_product_execution_root(),
        )
        broker = ApprovalBroker()
        context = ToolRunContext.create("")
        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        installed_name = "skill-creator"
        command = subprocess.list2cmdline(
            [
                str(base_executable),
                "scripts/install-skill-from-github.py",
                "--repo",
                "openai/skills",
                "--path",
                "skills/.system/skill-creator",
                "--name",
                installed_name,
                "--method",
                "auto",
            ]
        )
        args = {
            "cmd": command,
            "skill": {
                "id": installer.id,
                "entry": "scripts/install-skill-from-github.py",
                "workdir": ".",
            },
            "access": {
                "filesystem": "read_only",
                "additional_roots": [
                    {
                        "locator": {"kind": "standard", "name": "personal_skills"},
                        "access": "read_write",
                    },
                    {"path": str(base_executable.parent), "access": "read_only"},
                ],
                "network": "enabled",
            },
            "reason": "verify public Skill installation into an isolated personal catalog",
            COMMAND_SESSION_ID_ARG: "smoke",
            COMMAND_RUN_ID_ARG: "smoke-skill-install",
            COMMAND_TOOL_CALL_ID_ARG: "smoke-skill-install",
            COMMAND_SURFACE_ARG: "desktop_chat",
            COMMAND_RUN_CONTEXT_ARG: context,
            COMMAND_SKILL_SNAPSHOT_ARG: turn_snapshot,
        }
        result_box: list[Any] = []
        thread = threading.Thread(
            target=lambda: result_box.append(
                ExecCommandTool(
                    runtime,
                    broker,
                    personal_skills_root=personal_root,
                ).invoke(args)
            ),
            daemon=True,
        )
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
                raise RuntimeError("Skill install command did not request approval")
            request = pending[0]
            if request.details.workdir != "." or request.details.skill is None:
                raise RuntimeError("Skill install command exposed an internal workdir")
            if not broker.decide(
                request.request_id,
                CommandApprovalDecision(allow=True),
                session_id="smoke",
                run_id="smoke-skill-install",
                expected_kind="command",
            ):
                raise RuntimeError("Skill install command approval was rejected")
            thread.join(timeout=90)
            if thread.is_alive() or not result_box:
                raise RuntimeError("Skill install command did not finish")
            payload = json.loads(result_box[0].text)
            if payload.get("status") != "exited" or payload.get("exit_code") != 0:
                raise RuntimeError(
                    "Skill install command failed: " + json.dumps(payload, ensure_ascii=False)
                )
            deadline = time.monotonic() + 10
            discovered = None
            while time.monotonic() < deadline:
                discovered = next(
                    (
                        item
                        for item in service.snapshot.entries
                        if item.source == "personal" and item.package_root.name == installed_name
                    ),
                    None,
                )
                if discovered is not None:
                    break
                time.sleep(0.1)
            if discovered is None:
                raise RuntimeError(
                    "catalog watcher did not discover the installed Skill: "
                    + json.dumps(
                        {
                            "generation": service.snapshot.generation,
                            "watcher": service.watcher_state.to_dict(),
                            "entries": [
                                {
                                    "id": item.id,
                                    "source": item.source,
                                    "package": item.package_root.name,
                                }
                                for item in service.snapshot.entries
                            ],
                            "issues": [issue.to_dict() for issue in service.snapshot.issues],
                            "destination_exists": (personal_root / installed_name).is_dir(),
                        },
                        ensure_ascii=False,
                    )
                )
            return {
                "status": "ok",
                "sandbox_type": payload.get("sandbox_type"),
                "installed": installed_name,
                "catalog_generation": service.snapshot.generation,
                "skill_id": discovered.id,
                "workdir": request.details.workdir,
            }
        finally:
            context.close_commands()
            runtime.close()
            service.stop()


def _ensure_windows_owner_inheritance(path: Path) -> None:
    """Make a temp smoke root match the explicit user ACE of the product data root."""
    if os.name != "nt":
        return
    identity = subprocess.run(
        ["whoami"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    completed = subprocess.run(
        ["icacls", str(path), "/grant:r", f"{identity}:(OI)(CI)(F)"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "failed to prepare isolated Skill smoke ACL: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


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
        "skill": {
            "id": record.id,
            "entry": "scripts/quick_validate.py",
            "workdir": ".",
        },
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
    permissions = sandbox.get("permissions", {})
    return runtime.start_process(
        argv=(
            _system_cmd(),
            "/D",
            "/S",
            "/C",
            command,
        ),
        workdir=root,
        env=_command_env(network_enabled=permissions.get("network") == "enabled"),
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


def _command_env(*, network_enabled: bool = False) -> dict[str, str]:
    env = {
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
    if network_enabled:
        from agent.command_runtime.runtime import safe_loopback_proxy_environment

        env.update(safe_loopback_proxy_environment(os.environ))
    return env


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "ensure",
            "prepare",
            "doctor",
            "path",
            "status",
            "setup",
            "smoke",
            "network-smoke",
            "skill-install-smoke",
        ),
    )
    args = parser.parse_args(argv)
    if args.action in {"ensure", "prepare", "setup"}:
        try:
            executable = ensure_source_runtime(ROOT)
        except Exception as error:
            if args.action == "ensure":
                print(_concise_error(error))
                return 1
            raise
    else:
        executable = _verify_source_runtime(ROOT)
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
    if args.action == "network-smoke":
        print(json.dumps(network_smoke(executable), ensure_ascii=False))
        return 0
    if args.action == "skill-install-smoke":
        print(json.dumps(skill_install_smoke(executable), ensure_ascii=False))
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
