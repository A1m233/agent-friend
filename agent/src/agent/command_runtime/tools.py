"""Model-facing governed local command tools."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from ..approvals import ApprovalBroker
from ..skills.models import SkillTurnSnapshot
from ..tool_runtime.context import ToolRunContext
from ..tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from ..tools.protocol import Tool, ToolResult
from .models import (
    CommandAdditionalRoot,
    CommandApprovalDetails,
    CommandProcessSnapshot,
    CommandSkillBinding,
    PendingCommandApproval,
)
from .runtime import CommandRuntime, CommandRuntimeError, sandbox_context
from .skill_snapshot import SkillExecutionSnapshot, prepare_skill_snapshot

COMMAND_SESSION_ID_ARG = "__agent_friend_current_session_id"
COMMAND_RUN_ID_ARG = "__agent_friend_current_run_id"
COMMAND_TOOL_CALL_ID_ARG = "__agent_friend_current_tool_call_id"
COMMAND_SURFACE_ARG = "__agent_friend_interaction_surface"
COMMAND_RUN_CONTEXT_ARG = "__agent_friend_tool_run_context"
COMMAND_SKILL_SNAPSHOT_ARG = "__agent_friend_skill_turn_snapshot"

MAX_COMMAND_CHARS = 32 * 1024
MAX_STDIN_BYTES = 64 * 1024
MAX_RESULT_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 900.0
MAX_TIMEOUT_SECONDS = 3600.0


def _result(payload: dict[str, object], *, error: bool = False) -> ToolResult:
    return ToolResult(
        text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        is_error=error,
        meta={"error_kind": payload.get("error")} if error else {},
    )


def _bounded_process_result(snapshot: CommandProcessSnapshot) -> dict[str, object]:
    payload = snapshot.to_dict(include_output=False)
    streams = [
        ("stdout", snapshot.stdout_tail),
        ("stderr", snapshot.stderr_tail),
        ("pty", snapshot.pty_tail),
    ]
    nonempty = [(name, value) for name, value in streams if value]
    quota = MAX_RESULT_OUTPUT_BYTES // max(1, len(nonempty))
    omitted = 0
    for name, value in streams:
        retained = value[-quota:] if value else b""
        omitted += len(value) - len(retained)
        payload[name] = retained.decode("utf-8", errors="replace")
    payload["dropped_bytes"] = snapshot.dropped_bytes + omitted
    return payload


class ExecCommandTool:
    name: ClassVar[str] = "exec_command"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = MAX_TIMEOUT_SECONDS + 30
    result_context_retention: ClassVar[str] = "recoverable"
    supports_invocation_control: ClassVar[bool] = True
    description: ClassVar[str] = (
        "在产品治理的 Codex 沙箱中运行本地命令。普通非交互命令使用 buffered："
        "调用保持 pending，直到退出、超时、取消或运行时失联，过程输出只推送给界面；"
        "交互式命令使用 streaming，尽早返回仅对当前 run 有效的 process_id。每次启动"
        "都必须向用户申请一次准确的命令、目录、文件系统与网络权限；不得假设批准。"
        "Windows 默认使用系统 cmd.exe；PowerShell 脚本只应显式调用可用的 pwsh.exe。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "minLength": 1, "maxLength": MAX_COMMAND_CHARS},
            "workdir": {"type": "string"},
            "mode": {"type": "string", "enum": ["buffered", "streaming"]},
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": MAX_TIMEOUT_SECONDS,
            },
            "access": {
                "type": "object",
                "properties": {
                    "filesystem": {
                        "type": "string",
                        "enum": ["read_only", "workspace_write"],
                    },
                    "additional_roots": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "minLength": 1},
                                "access": {
                                    "type": "string",
                                    "enum": ["read_only", "workspace_write"],
                                },
                            },
                            "required": ["path", "access"],
                            "additionalProperties": False,
                        },
                    },
                    "network": {"type": "string", "enum": ["disabled", "enabled"]},
                },
                "additionalProperties": False,
            },
            "skill": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "entry": {"type": "string", "minLength": 1},
                },
                "required": ["id", "entry"],
                "additionalProperties": False,
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "required": ["cmd", "workdir", "reason"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        runtime: CommandRuntime,
        broker: ApprovalBroker | None,
        *,
        base_env: dict[str, str] | None = None,
    ) -> None:
        self._runtime = runtime
        self._broker = broker
        self._base_env = dict(base_env or os.environ)

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        if args.get(COMMAND_SURFACE_ARG) != "desktop_chat" or self._broker is None:
            return _result(
                {
                    "status": "unavailable",
                    "error": "approval_surface_unavailable",
                    "message": "当前入口不能展示命令授权，请在桌面聊天中继续。",
                },
                error=True,
            )
        if not self._runtime.available:
            return _result(
                {
                    "status": "unavailable",
                    "error": "command_runtime_unavailable",
                    "message": "产品内置的 Codex 命令运行时不可用。",
                },
                error=True,
            )
        if not self._runtime.windows_sandbox_ready:
            return _result(
                {
                    "status": "unavailable",
                    "error": "windows_sandbox_setup_required",
                    "message": (
                        "需要先由你显式初始化 Windows 命令沙箱；普通命令不会自动触发管理员授权。"
                    ),
                },
                error=True,
            )
        try:
            context = self._context(args)
            session_id, run_id, tool_call_id = self._ownership(args)
            details, workdir, roots, run_temp, path_identities = self._parse(
                args, context, run_id=run_id
            )
        except (TypeError, ValueError, CommandRuntimeError) as exc:
            return _result(
                {
                    "status": "denied",
                    "error": getattr(exc, "code", "invalid_arguments"),
                    "message": str(exc),
                },
                error=True,
            )
        request = PendingCommandApproval.create(
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            reason=str(args["reason"]).strip(),
            details=details,
        )
        decision = self._broker.request(request, timeout_seconds=300.0)
        if decision is None:
            return _result(
                {
                    "status": "expired",
                    "request_id": request.request_id,
                    "error": "approval_expired",
                },
                error=True,
            )
        if request.status == "cancelled":
            return _result(
                {
                    "status": "cancelled",
                    "request_id": request.request_id,
                    "error": "approval_cancelled",
                },
                error=True,
            )
        if not decision.approved:
            return _result(
                {
                    "status": "denied",
                    "request_id": request.request_id,
                    "error": "approval_denied",
                },
                error=True,
            )
        try:
            self._validate_path_identities(path_identities)
        except CommandRuntimeError as exc:
            return _result({"status": "failed", "error": exc.code, "message": str(exc)}, error=True)
        sandbox = sandbox_context(
            workdir=workdir,
            writable=details.filesystem_access == "workspace_write",
            additional_roots=roots,
            network_enabled=details.network_access == "enabled",
        )
        try:
            snapshot = self._runtime.start_process(
                argv=details.argv,
                workdir=workdir,
                env=self._child_env(run_temp),
                mode=details.mode,
                session_id=session_id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                sandbox=sandbox,
            )
        except CommandRuntimeError as exc:
            return _result({"status": "failed", "error": exc.code, "message": str(exc)}, error=True)

        def terminate(reason: str) -> None:
            self._terminate_for_context(snapshot.process_id, session_id, run_id, reason)

        context.register_command_handle(snapshot.process_id, terminate)
        control = args.get(TOOL_INVOCATION_CONTROL_ARG)
        callback_token = (
            control.add_cancel_callback(terminate)
            if isinstance(control, ToolInvocationControl)
            else None
        )
        if details.mode == "streaming":
            return _result(
                {
                    "status": "running",
                    "process_id": snapshot.process_id,
                    "output_seq": snapshot.output_seq,
                    "sandbox_type": snapshot.sandbox_type,
                }
            )
        try:
            snapshot = self._runtime.wait(
                snapshot.process_id,
                session_id=session_id,
                run_id=run_id,
                timeout_seconds=details.timeout_seconds,
            )
            if not snapshot.terminal():
                snapshot = self._runtime.terminate(
                    snapshot.process_id,
                    session_id=session_id,
                    run_id=run_id,
                    reason="timeout",
                )
        finally:
            context.release_command_handle(snapshot.process_id)
            if callback_token is not None and isinstance(control, ToolInvocationControl):
                control.remove_cancel_callback(callback_token)
        return _result(
            _bounded_process_result(snapshot),
            error=snapshot.status not in {"exited"} or snapshot.exit_code != 0,
        )

    def _parse(
        self, args: dict[str, Any], context: ToolRunContext, *, run_id: str
    ) -> tuple[
        CommandApprovalDetails,
        Path,
        tuple[tuple[Path, bool], ...],
        Path,
        tuple[tuple[Path, tuple[int, int, int]], ...],
    ]:
        cmd = args.get("cmd")
        reason = args.get("reason")
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_COMMAND_CHARS:
            raise ValueError("cmd 无效")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
            raise ValueError("reason 无效")
        mode = args.get("mode", "buffered")
        if mode not in {"buffered", "streaming"}:
            raise ValueError("mode 无效")
        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds 无效")
        raw_workdir = args.get("workdir")
        if not isinstance(raw_workdir, str) or not Path(raw_workdir).expanduser().is_absolute():
            raise ValueError("workdir 必须是绝对路径")
        requested_path = Path(raw_workdir).expanduser()
        self._reject_linked_path(requested_path)
        requested_workdir = requested_path.resolve()
        if not requested_workdir.is_dir():
            raise ValueError("workdir 必须是已存在目录")
        access = args.get("access", {})
        if not isinstance(access, dict):
            raise ValueError("access 无效")
        filesystem = access.get("filesystem", "read_only")
        network = access.get("network", "disabled")
        if filesystem not in {"read_only", "workspace_write"}:
            raise ValueError("filesystem access 无效")
        if network not in {"disabled", "enabled"}:
            raise ValueError("network access 无效")
        roots = self._parse_roots(access.get("additional_roots", []))
        run_temp = self._run_temp(context, run_id)
        active_snapshot = args.get(COMMAND_SKILL_SNAPSHOT_ARG)
        active = active_snapshot if isinstance(active_snapshot, SkillTurnSnapshot) else None
        workdir = requested_workdir
        skill_binding: CommandSkillBinding | None = None
        raw_skill = args.get("skill")
        if raw_skill is None:
            self._reject_unbound_skill_paths(cmd, requested_workdir, active)
        else:
            if not isinstance(raw_skill, dict):
                raise ValueError("skill 无效")
            skill_id = raw_skill.get("id")
            entry = raw_skill.get("entry")
            if not isinstance(skill_id, str) or not isinstance(entry, str):
                raise ValueError("skill.id 和 skill.entry 必须是字符串")
            activation = next(
                (
                    item
                    for item in (active.activations if active is not None else ())
                    if item.record.id == skill_id
                ),
                None,
            )
            if activation is None:
                raise CommandRuntimeError(
                    "skill_not_active", "Skill binding must reference an activated Skill"
                )
            skill_snapshot = prepare_skill_snapshot(
                context,
                activation,
                entry=entry,
                execution_root=run_temp.parent / "skills",
            )
            workdir = self._rewrite_skill_workdir(requested_workdir, skill_snapshot)
            cmd = self._rewrite_skill_entry(cmd, skill_snapshot)
            roots = (*roots, (skill_snapshot.snapshot_root, False))
            skill_binding = CommandSkillBinding(
                skill_id=skill_snapshot.skill_id,
                entry=skill_snapshot.entry,
                fingerprint=skill_snapshot.fingerprint,
            )
        roots = (*roots, (run_temp, True))
        argv = self._shell_argv(cmd, mode)
        root_details = tuple(
            CommandAdditionalRoot(
                path=str(path), access="workspace_write" if writable else "read_only"
            )
            for path, writable in roots
            if path != run_temp and (skill_binding is None or path != skill_snapshot.snapshot_root)
        )
        return (
            CommandApprovalDetails(
                cmd=str(args["cmd"]),
                argv=argv,
                workdir=str(requested_workdir),
                mode=mode,
                timeout_seconds=float(timeout),
                filesystem_access=filesystem,
                additional_roots=root_details,
                network_access=network,
                interactive=mode == "streaming",
                skill=skill_binding,
            ),
            workdir,
            roots,
            run_temp,
            tuple((path, self._path_identity(path)) for path in {workdir, *[p for p, _ in roots]}),
        )

    def _run_temp(self, context: ToolRunContext, run_id: str) -> Path:
        key = "command-run-root"
        existing = context.command_resource(key)
        if isinstance(existing, Path):
            return existing / "temp"
        root = self._runtime.execution_root / f"run-{uuid4().hex}"
        temp = root / "temp"
        temp.mkdir(parents=True, exist_ok=False)
        registered = context.register_command_resource(
            key, root, lambda: shutil.rmtree(root, ignore_errors=True)
        )
        assert isinstance(registered, Path)
        return registered / "temp"

    @staticmethod
    def _rewrite_skill_workdir(requested_workdir: Path, snapshot: SkillExecutionSnapshot) -> Path:
        try:
            relative = requested_workdir.relative_to(snapshot.source_root)
        except ValueError as exc:
            raise CommandRuntimeError(
                "skill_workdir_invalid",
                "A Skill-bound command must use a workdir inside that Skill package",
            ) from exc
        rewritten = snapshot.snapshot_root / relative
        if not rewritten.is_dir():
            raise CommandRuntimeError(
                "skill_workdir_invalid", "Skill workdir is not a directory in the snapshot"
            )
        return rewritten

    @staticmethod
    def _rewrite_skill_entry(cmd: str, snapshot: SkillExecutionSnapshot) -> str:
        absolute_source = str(snapshot.source_root / Path(*snapshot.entry.split("/")))
        if absolute_source in cmd:
            return cmd.replace(absolute_source, str(snapshot.entry_path))
        native_entry = str(Path(*snapshot.entry.split("/")))
        if snapshot.entry not in cmd and native_entry not in cmd:
            raise CommandRuntimeError(
                "skill_entry_not_used", "Command does not reference the declared Skill entry"
            )
        return cmd

    @staticmethod
    def _reject_unbound_skill_paths(
        cmd: str, workdir: Path, snapshot: SkillTurnSnapshot | None
    ) -> None:
        for activation in snapshot.activations if snapshot is not None else ():
            root = activation.record.package_root.resolve()
            try:
                workdir.relative_to(root)
            except ValueError:
                pass
            else:
                raise CommandRuntimeError(
                    "skill_binding_required",
                    "Commands using an activated Skill package must provide structured skill binding",
                )
            if str(root) in cmd or root.as_posix() in cmd:
                raise CommandRuntimeError(
                    "skill_binding_required",
                    "Commands using an activated Skill path must provide structured skill binding",
                )

    @staticmethod
    def _parse_roots(raw: object) -> tuple[tuple[Path, bool], ...]:
        if not isinstance(raw, list) or len(raw) > 8:
            raise ValueError("additional_roots 无效")
        roots: list[tuple[Path, bool]] = []
        for item in raw:
            if not isinstance(item, dict) or item.get("access") not in {
                "read_only",
                "workspace_write",
            }:
                raise ValueError("additional_roots 项无效")
            path = item.get("path")
            if not isinstance(path, str) or not path or not Path(path).expanduser().is_absolute():
                raise ValueError("additional root path 必须是绝对路径")
            requested = Path(path).expanduser()
            ExecCommandTool._reject_linked_path(requested)
            resolved = requested.resolve()
            if not resolved.exists():
                raise ValueError("additional root 必须存在")
            roots.append((resolved, item["access"] == "workspace_write"))
        return tuple(roots)

    @staticmethod
    def _reject_linked_path(path: Path) -> None:
        absolute = path.absolute()
        is_junction = getattr(os.path, "isjunction", lambda _path: False)
        for candidate in (absolute, *absolute.parents):
            if candidate.is_symlink() or is_junction(candidate):
                raise CommandRuntimeError(
                    "linked_path_not_allowed",
                    f"Command paths cannot traverse links or junctions: {candidate}",
                )

    @staticmethod
    def _path_identity(path: Path) -> tuple[int, int, int]:
        value = path.stat(follow_symlinks=False)
        return value.st_dev, value.st_ino, value.st_mode

    @classmethod
    def _validate_path_identities(
        cls, values: tuple[tuple[Path, tuple[int, int, int]], ...]
    ) -> None:
        for path, expected in values:
            try:
                observed = cls._path_identity(path)
            except OSError as exc:
                raise CommandRuntimeError(
                    "path_changed_before_start", f"Approved path disappeared: {path}"
                ) from exc
            if observed != expected:
                raise CommandRuntimeError(
                    "path_changed_before_start", f"Approved path changed before start: {path}"
                )

    def _shell_argv(self, cmd: str, mode: object) -> tuple[str, ...]:
        if os.name == "nt":
            system_root = self._base_env.get("SystemRoot") or self._base_env.get("WINDIR")
            if not system_root:
                raise CommandRuntimeError(
                    "windows_shell_unavailable", "Windows system root is unavailable"
                )
            command_prompt = Path(system_root) / "System32" / "cmd.exe"
            if not command_prompt.is_file():
                raise CommandRuntimeError(
                    "windows_shell_unavailable", "System cmd.exe is unavailable"
                )
            return (str(command_prompt), "/D", "/S", "/C", cmd)
        return ("/bin/sh", "-lc", cmd)

    @staticmethod
    def _context(args: dict[str, Any]) -> ToolRunContext:
        context = args.get(COMMAND_RUN_CONTEXT_ARG)
        if not isinstance(context, ToolRunContext):
            raise ValueError("缺少当前 run 的命令上下文")
        return context

    @staticmethod
    def _ownership(args: dict[str, Any]) -> tuple[str, str, str]:
        session_id = str(args.get(COMMAND_SESSION_ID_ARG) or "")
        run_id = str(args.get(COMMAND_RUN_ID_ARG) or "")
        tool_call_id = str(args.get(COMMAND_TOOL_CALL_ID_ARG) or "")
        if not session_id or not run_id or not tool_call_id:
            raise ValueError("缺少命令运行归属")
        return session_id, run_id, tool_call_id

    def _child_env(self, run_temp: Path) -> dict[str, str]:
        allowed = {
            "PATH",
            "PATHEXT",
            "SystemRoot",
            "WINDIR",
            "COMSPEC",
            "LANG",
            "LC_ALL",
            "TMP",
            "TEMP",
            "TMPDIR",
            "HOME",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "APPDATA",
            "LOCALAPPDATA",
        }
        env = {key: value for key, value in self._base_env.items() if key in allowed}
        env.update({"TEMP": str(run_temp), "TMP": str(run_temp), "TMPDIR": str(run_temp)})
        return env

    def _terminate_for_context(
        self, process_id: str, session_id: str, run_id: str, reason: str
    ) -> None:
        self._runtime.terminate(process_id, session_id=session_id, run_id=run_id, reason=reason)


class WriteStdinTool:
    name: ClassVar[str] = "write_stdin"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 65.0
    result_context_retention: ClassVar[str] = "recoverable"
    description: ClassVar[str] = (
        "向当前 run 中由 exec_command(streaming) 创建的进程写入输入，或显式等待"
        "新输出/退出。不要为了观察进度高频轮询；只有进程需要真实输入，或界面推送"
        "表明状态需要再次判断时调用。process_id 不能跨 run 使用。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "process_id": {"type": "string", "minLength": 1},
            "chars": {"type": "string", "maxLength": MAX_STDIN_BYTES},
            "wait_for": {"type": "string", "enum": ["none", "output", "exit"]},
            "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60},
            "after_seq": {"type": "integer", "minimum": 0},
        },
        "required": ["process_id"],
        "additionalProperties": False,
    }

    def __init__(self, runtime: CommandRuntime) -> None:
        self._runtime = runtime

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        try:
            context = ExecCommandTool._context(args)
            session_id, run_id, _ = ExecCommandTool._ownership(args)
            process_id = args.get("process_id")
            if not isinstance(process_id, str) or not context.owns_command_handle(process_id):
                raise ValueError("process_id 不属于当前 run")
            chars = args.get("chars")
            if chars is not None:
                if not isinstance(chars, str) or len(chars.encode("utf-8")) > MAX_STDIN_BYTES:
                    raise ValueError("chars 无效或过长")
                self._runtime.write(
                    process_id, chars.encode("utf-8"), session_id=session_id, run_id=run_id
                )
            wait_for = args.get("wait_for", "none")
            timeout = args.get("timeout_seconds", 0)
            if wait_for not in {"none", "output", "exit"}:
                raise ValueError("wait_for 无效")
            if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 60:
                raise ValueError("timeout_seconds 无效")
            snapshot = self._runtime.wait(
                process_id,
                session_id=session_id,
                run_id=run_id,
                timeout_seconds=float(timeout),
                wait_for=wait_for,
                after_seq=args.get("after_seq") if isinstance(args.get("after_seq"), int) else None,
            )
            if snapshot.terminal():
                context.release_command_handle(process_id)
            return _result(_bounded_process_result(snapshot))
        except (ValueError, CommandRuntimeError) as exc:
            return _result(
                {
                    "status": "failed",
                    "error": getattr(exc, "code", "invalid_arguments"),
                    "message": str(exc),
                },
                error=True,
            )


def make_command_tools(
    runtime: CommandRuntime,
    broker: ApprovalBroker | None,
) -> list[Tool]:
    return [
        ExecCommandTool(runtime, broker),
        WriteStdinTool(runtime),
    ]
