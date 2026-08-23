"""Model-facing governed local command tools."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from ..approvals import ApprovalBroker
from ..execution_environment import (
    ExecutionEnvironmentManager,
    ManagedEnvironmentError,
    ResolvedManagedCommand,
)
from ..filesystem.policy import resolve_locator
from ..paths import command_workspaces_dir, skills_dir
from ..skills.models import SkillTurnSnapshot
from ..tool_runtime.context import ToolRunContext
from ..tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from ..tools.protocol import Tool, ToolResult
from .models import (
    CommandAdditionalRoot,
    CommandApprovalDetails,
    CommandExecution,
    CommandProcessSnapshot,
    CommandSkillBinding,
    PendingCommandApproval,
    decode_process_output,
)
from .runtime import (
    CommandRuntime,
    CommandRuntimeError,
    _env_value,
    safe_loopback_proxy_environment,
    sandbox_context,
)
from .skill_snapshot import SkillExecutionSnapshot, prepare_skill_snapshot
from .workspace import session_workspace_path

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
MAX_COMMAND_REASON_CHARS = 300
MAX_SESSION_WORKSPACE_FILES = 2_000
MAX_SESSION_WORKSPACE_FILE_BYTES = 25 * 1024 * 1024
MAX_SESSION_WORKSPACE_BYTES = 100 * 1024 * 1024
_WINDOWS_CMD_BUILTINS = {
    "assoc",
    "break",
    "call",
    "cd",
    "chdir",
    "cls",
    "color",
    "copy",
    "date",
    "del",
    "dir",
    "echo",
    "endlocal",
    "erase",
    "exit",
    "for",
    "ftype",
    "goto",
    "if",
    "md",
    "mkdir",
    "mklink",
    "move",
    "path",
    "pause",
    "popd",
    "prompt",
    "pushd",
    "rd",
    "rem",
    "ren",
    "rename",
    "rmdir",
    "set",
    "setlocal",
    "shift",
    "start",
    "time",
    "title",
    "type",
    "ver",
    "verify",
    "vol",
}
_WINDOWS_SHELL_OPERATORS = {"&", "&&", "|", "||", ">", ">>", "<"}
_WINDOWS_POWERSHELL_EXECUTABLES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}


def _result(payload: dict[str, object], *, error: bool = False) -> ToolResult:
    error_kind = payload.get("error")
    meta: dict[str, object] = {}
    if error and isinstance(error_kind, str) and error_kind:
        meta["error_kind"] = error_kind
    if error_kind in {"windows_sandbox_setup_required", "command_runtime_unavailable"}:
        meta["requires_user_action"] = True
        meta["no_progress_family"] = error_kind
    elif error_kind in {
        "host_path_not_found",
        "host_executable_not_found",
        "host_user_requires_argv",
        "host_user_access_is_unrestricted",
        "host_user_skill_binding_unsupported",
        "powershell_syntax_in_cmd",
        "windows_executable_path_requires_argv",
        "structured_executable_must_be_absolute",
        "windows_powershell_unsupported",
        "windows_process_fast_fail",
        "windows_process_failed",
        "powershell_script_not_executed",
    }:
        meta["no_progress_family"] = error_kind
    return ToolResult(
        text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        is_error=error,
        meta=meta,
    )


def _command_runtime_unavailable_result() -> ToolResult:
    return _result(
        {
            "status": "unavailable",
            "error": "command_runtime_unavailable",
            "message": (
                "当前 agent-friend 运行实例没有加载产品命令运行时，本轮无法通过重试或"
                "文件授权恢复。请让用户重启当前运行实例（开发环境即重启 dev）；重启后仍"
                "不可用时，需要更新或修复产品安装，然后由用户重新发起命令。不要原样重试、"
                "申请‘全局系统权限’或声称会在后台继续。"
            ),
            "retryable": False,
            "retry_after": "new_runtime_with_command_runtime_available",
            "required_action": "restart_runtime_then_update_or_repair_if_unavailable",
            "blocking_scope": "current_app_runtime",
        },
        error=True,
    )


def _command_runtime_error_result(
    exc: CommandRuntimeError,
    *,
    fallback_status: str,
) -> ToolResult:
    if exc.code == "command_runtime_unavailable":
        return _command_runtime_unavailable_result()
    return _result(
        {"status": fallback_status, "error": exc.code, "message": str(exc)},
        error=True,
    )


def _managed_environment_error_result(exc: ManagedEnvironmentError) -> ToolResult:
    return _result(
        {
            "status": "unavailable",
            "error": exc.code,
            "message": str(exc),
            "executed": False,
            "provider": "python",
        },
        error=True,
    )


def _optional_command_reason(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_COMMAND_REASON_CHARS:
        raise ValueError("reason 无效")
    normalized = value.strip()
    return normalized or None


def _default_command_reason(details: CommandApprovalDetails) -> str:
    if details.execution == "host_user":
        return "以宿主用户身份运行本地命令"
    return "在 Codex 沙箱中运行本地命令"


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
        payload[name] = decode_process_output(retained)
    payload["dropped_bytes"] = snapshot.dropped_bytes + omitted
    return payload


class ExecCommandTool:
    name: ClassVar[str] = "exec_command"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = MAX_TIMEOUT_SECONDS + 30
    result_context_retention: ClassVar[str] = "recoverable"
    supports_invocation_control: ClassVar[bool] = True
    description: ClassVar[str] = (
        "运行经过产品治理和逐次授权的本地命令。默认 execution=sandboxed，在 Codex 沙箱中"
        "执行；只有桌面程序或确实无法在受限沙箱工作的宿主能力才使用 execution=host_user。"
        "host_user 会以当前桌面用户身份、不受文件系统和网络沙箱限制地启动，因此必须使用"
        "结构化 argv，不能提供 shell 命令字符串、Skill 绑定或 access。普通非交互命令使用 buffered："
        "调用保持 pending，直到退出、超时、取消或运行时失联，过程输出只推送给界面；"
        "交互式命令使用 streaming，尽早返回仅对当前 run 有效的 process_id。每次启动"
        "都必须向用户申请一次准确的命令、目录、文件系统与网络权限；不得假设批准。"
        "普通命令可省略 workdir，此时使用当前会话跨轮次保留的受治理目录；显式"
        " workdir 必须是已存在的绝对路径且只覆盖本次命令。Skill-bound 命令省略顶层"
        " workdir，并在 skill.workdir 使用包内相对目录（默认包根）；不要猜测或读取"
        "产品生成的 Skill 安装绝对路径。会创建、下载或修改文件的命令必须申请"
        " read_write。Windows 默认使用系统 cmd.exe，不能直接执行 Get-Command 等"
        "PowerShell cmdlet；确需 PowerShell 时必须在 cmd 中显式调用可用的 pwsh.exe。"
        "直接启动一个程序及其参数时优先使用 argv；只有确实需要 shell 内建命令、管道、"
        "重定向、变量展开等 shell 解释，或运行 Skill-bound 脚本时才使用 cmd。"
        "sandboxed 模式中的 python/python3 会稳定解析到产品管理、按会话隔离的 Python，"
        "不依赖用户 PATH；pip/pip3/python -m pip/uv pip 会规范化为同一受管 package"
        " operation。registry 安装必须申请 read_write 和 network=enabled；已授权本地 .whl"
        " 只需 read_write。包操作首期只接受 wheel，不支持源码构建、editable、VCS、URL 或"
        "自定义 index；复合 shell 包命令必须改用 argv。"
        "系统 powershell.exe 在当前 Codex Windows 沙箱中存在已知启动故障，宿主会在"
        "执行前明确拒绝，不能把脚本回显和退出码 0 当作成功。pwsh.exe 由宿主转换成"
        "直接 argv，避免 cmd.exe 与 exec-server 二次解析引号。"
        "Windows 受限沙箱中的找不到文件或拒绝访问只描述该沙箱进程的视角，不能据此"
        "断言宿主机未安装软件；结果中的 host_path_validation 会明确哪些路径已由宿主验证。"
        "宿主会解析能够确定的 PATH 启动程序，并自动把必要的最小程序目录作为只读派生"
        "范围纳入同一次授权；不要猜测安装目录或为此修改 workdir。首次使用 Windows 沙箱时，原命令会保持等待，直到用户完成初始化"
        "并批准、拒绝、超时或取消；不要改写命令来绕过初始化。"
        "若结果为 command_runtime_unavailable，当前运行实例内不可恢复：停止重试，说明需"
        "重启运行实例，若仍不可用则更新或修复安装；不要改申请文件权限或所谓全局系统权限。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "argv": {
                "type": "array",
                "minItems": 1,
                "maxItems": 256,
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "结构化进程参数，第一个元素是绝对可执行文件或宿主 PATH 中的程序名。"
                    "直接启动单个 CLI 或桌面程序时优先使用本字段；每个参数独立成一个数组"
                    "元素，因此正则中的 |、含空格路径和引号不会被 shell 再解释。"
                    "含空格的 Windows 路径必须用本字段；host_user 必须使用 argv。"
                ),
            },
            "cmd": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_COMMAND_CHARS,
                "description": (
                    "需要 shell 解释的完整命令字符串，只用于 shell 内建命令、管道、重定向、"
                    "变量展开或 Skill-bound 脚本；普通单程序及参数优先使用 argv。Windows 中"
                    "本字段由 cmd.exe 解释；PowerShell cmdlet 必须显式通过可用的"
                    " pwsh.exe -NoProfile -Command 调用，并把脚本作为一个带双引号的参数。"
                    "Skill-bound 命令必须引用 skill.entry 的包内相对路径，不能包含 Skill"
                    "安装目录或其它内部绝对路径。host_user 禁止使用本字段。"
                ),
            },
            "execution": {
                "type": "string",
                "enum": ["sandboxed", "host_user"],
                "default": "sandboxed",
                "description": (
                    "sandboxed 使用受限 Codex 沙箱；host_user 以当前桌面用户身份运行且不受"
                    "文件系统或网络沙箱限制，只用于用户明确可见且沙箱不兼容的宿主能力。"
                ),
            },
            "workdir": {
                "type": "string",
                "description": (
                    "普通命令可选的已存在绝对目录，仅覆盖本次命令；省略时使用当前会话的"
                    "受治理默认目录。Skill-bound 命令不得提供本字段，应使用 skill.workdir。"
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["buffered", "streaming"],
                "default": "buffered",
                "description": (
                    "普通非交互命令用 buffered 并等待终态；只有需要持续输入的交互式进程"
                    "才用 streaming。"
                ),
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": MAX_TIMEOUT_SECONDS,
                "default": DEFAULT_TIMEOUT_SECONDS,
                "description": "命令最长运行秒数；宿主默认 120 秒，最长 3600 秒。",
            },
            "access": {
                "type": "object",
                "description": "本次命令需要的文件系统和网络能力；只申请完成任务所需的最小范围。",
                "properties": {
                    "filesystem": {
                        "type": "string",
                        "enum": ["read_only", "read_write"],
                        "default": "read_only",
                        "description": "只读命令用 read_only；会创建、下载、修改或删除文件时用 read_write。",
                    },
                    "additional_roots": {
                        "type": "array",
                        "maxItems": 8,
                        "description": (
                            "命令确实需要访问的额外已存在根目录。Windows 中关闭 ACL 继承的"
                            "用户级程序目录即使字面上位于 workdir 下，也必须作为窄 read_only 根明确申请。"
                        ),
                        "items": {
                            "type": "object",
                            "description": "命令工作目录之外确实需要访问的额外根；path 与 locator 二选一。",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "已存在的绝对目录；不能填相对路径或 Skill 物化路径。",
                                },
                                "locator": {
                                    "type": "object",
                                    "description": "由宿主解析的路径定位器；kind 决定必须提供 path 还是 name。",
                                    "properties": {
                                        "kind": {
                                            "type": "string",
                                            "enum": ["path", "standard"],
                                            "description": "绝对路径用 path；宿主标准位置用 standard。",
                                        },
                                        "path": {
                                            "type": "string",
                                            "description": "kind=path 时必填的绝对目录。",
                                        },
                                        "name": {
                                            "type": "string",
                                            "enum": [
                                                "home",
                                                "desktop",
                                                "documents",
                                                "downloads",
                                                "agent_data",
                                                "agent_logs",
                                                "personal_skills",
                                                "shared_skills",
                                            ],
                                            "description": "kind=standard 时必填的宿主标准位置名称。",
                                        },
                                    },
                                    "required": ["kind"],
                                    "oneOf": [
                                        {
                                            "properties": {"kind": {"const": "path"}},
                                            "required": ["path"],
                                        },
                                        {
                                            "properties": {"kind": {"const": "standard"}},
                                            "required": ["name"],
                                        },
                                    ],
                                    "additionalProperties": False,
                                },
                                "access": {
                                    "type": "string",
                                    "enum": ["read_only", "read_write"],
                                    "description": "该额外根的最小访问级别。",
                                },
                            },
                            "required": ["access"],
                            "oneOf": [
                                {"required": ["path"]},
                                {"required": ["locator"]},
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "network": {
                        "type": "string",
                        "enum": ["disabled", "enabled"],
                        "default": "disabled",
                        "description": "只有命令确实需要联网时使用 enabled；默认禁用网络。",
                    },
                },
                "additionalProperties": False,
            },
            "skill": {
                "type": "object",
                "description": (
                    "运行当前用户轮已激活 Skill 中的脚本。只有一个 Skill 已激活时可省略 "
                    "skill_id 由宿主绑定；多个 Skill 已激活时必须提供精确 skill_id。上一用户轮的"
                    "激活不会沿用；当前轮尚未激活目标 Skill 时先调用 skill_read。"
                ),
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "已激活 Skill 结果中的精确 skill_id。不能填 filesystem_scope_id、"
                            "request_id、名称或路径；只有一个 Skill 已激活时应省略，由宿主绑定。"
                        ),
                    },
                    "entry": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Skill 指令中声明的包内脚本相对路径，例如 scripts/run.py。",
                    },
                    "workdir": {
                        "type": "string",
                        "minLength": 1,
                        "default": ".",
                        "description": "Skill 包内相对工作目录，默认包根；不得使用绝对路径或 ..。",
                    },
                },
                "required": ["entry"],
                "additionalProperties": False,
            },
            "reason": {
                "type": "string",
                "maxLength": MAX_COMMAND_REASON_CHARS,
                "description": (
                    "可选的用户可见执行理由，准确说明命令为何是当前任务所需；"
                    "省略或留空时由宿主根据规范化执行级别生成稳定展示文案。"
                ),
            },
        },
        "oneOf": [
            {"required": ["cmd"], "not": {"required": ["argv"]}},
            {"required": ["argv"], "not": {"required": ["cmd"]}},
        ],
        "allOf": [
            {
                "if": {
                    "properties": {"execution": {"const": "host_user"}},
                    "required": ["execution"],
                },
                "then": {
                    "required": ["argv"],
                    "not": {
                        "anyOf": [
                            {"required": ["cmd"]},
                            {"required": ["access"]},
                            {"required": ["skill"]},
                        ]
                    },
                },
            },
            {
                "if": {"required": ["skill"]},
                "then": {
                    "required": ["cmd"],
                    "properties": {"execution": {"const": "sandboxed"}},
                    "not": {
                        "anyOf": [
                            {"required": ["argv"]},
                            {"required": ["workdir"]},
                        ]
                    },
                },
            },
        ],
        "not": {"required": ["workdir", "skill"]},
        "additionalProperties": False,
    }

    def __init__(
        self,
        runtime: CommandRuntime,
        broker: ApprovalBroker | None,
        *,
        base_env: dict[str, str] | None = None,
        personal_skills_root: Path | None = None,
        session_workspaces_root: Path | None = None,
        execution_environment_manager: ExecutionEnvironmentManager | None = None,
    ) -> None:
        self._runtime = runtime
        self._broker = broker
        self._base_env = dict(base_env or os.environ)
        self._personal_skills_root = (personal_skills_root or skills_dir()).resolve()
        self._session_workspaces_root = (
            session_workspaces_root or command_workspaces_dir()
        ).resolve()
        self._execution_environment_manager = execution_environment_manager

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
            return _command_runtime_unavailable_result()
        try:
            approval_reason = _optional_command_reason(args.get("reason"))
            context = self._context(args)
            session_id, run_id, tool_call_id = self._ownership(args)
            details, workdir, roots, run_temp, path_identities = self._parse(
                args, context, session_id=session_id, run_id=run_id
            )
            approval_reason = approval_reason or _default_command_reason(details)
        except ManagedEnvironmentError as exc:
            return _managed_environment_error_result(exc)
        except CommandRuntimeError as exc:
            return _command_runtime_error_result(exc, fallback_status="denied")
        except (TypeError, ValueError) as exc:
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
            reason=approval_reason,
            details=details,
        )
        approval_started = time.monotonic()
        decision = self._broker.request(request, timeout_seconds=300.0)
        approval_wait_seconds = time.monotonic() - approval_started
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
        if details.execution == "sandboxed" and not self._runtime.windows_sandbox_ready:
            # The approval endpoint normally prevents this transition. Keep a
            # fail-closed check for bridge/runtime races without coupling setup
            # to the command invocation itself.
            return _result(
                {
                    "status": "failed",
                    "error": "windows_sandbox_became_unavailable",
                    "message": "Windows 命令沙箱在批准后失去就绪状态，请修复后重新发起命令。",
                },
                error=True,
            )
        try:
            self._validate_path_identities(path_identities)
        except CommandRuntimeError as exc:
            return _command_runtime_error_result(exc, fallback_status="failed")
        sandbox = (
            sandbox_context(
                workdir=workdir,
                writable=details.filesystem_access == "read_write",
                additional_roots=roots,
                network_enabled=details.network_access == "enabled",
            )
            if details.execution == "sandboxed"
            else None
        )
        launch_started = time.monotonic()
        try:
            if (
                sandbox is not None
                and details.cwd_source == "session_managed"
                and details.filesystem_access == "read_only"
            ):
                self._runtime.prepare_owned_read_only_workdir(workdir, sandbox)
            snapshot = self._runtime.start_process(
                argv=details.launch_argv or details.argv,
                workdir=workdir,
                env=(
                    self._host_user_env()
                    if details.execution == "host_user"
                    else self._child_env(
                        run_temp,
                        network_enabled=details.network_access == "enabled",
                        overrides=details.environment_overrides,
                    )
                ),
                mode=details.mode,
                session_id=session_id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                sandbox=sandbox,
            )
        except CommandRuntimeError as exc:
            return _command_runtime_error_result(exc, fallback_status="failed")

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
                    "execution": details.execution,
                    "approval_wait_seconds": round(approval_wait_seconds, 3),
                    "launch_seconds": round(time.monotonic() - launch_started, 3),
                    **self._host_path_evidence(details),
                    **self._execution_environment_evidence(details),
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
        result_payload = _bounded_process_result(snapshot)
        result_payload.update(
            {
                "execution": details.execution,
                "approval_wait_seconds": round(approval_wait_seconds, 3),
                "launch_seconds": round(
                    max(
                        0.0, time.monotonic() - launch_started - (snapshot.duration_seconds or 0.0)
                    ),
                    3,
                ),
            }
        )
        result_payload.update(self._host_path_evidence(details))
        result_error = snapshot.status not in {"exited"} or snapshot.exit_code != 0
        execution_environment = details.execution_environment
        if (
            not result_error
            and details.managed_command is not None
            and details.managed_command.package_operation
            and details.managed_command.requires_write
            and self._execution_environment_manager is not None
        ):
            try:
                updated = self._execution_environment_manager.record_command_success(
                    session_id,
                    run_id,
                    details.managed_command,
                )
            except ManagedEnvironmentError as exc:
                result_payload.update(
                    {
                        "error": exc.code,
                        "message": str(exc),
                        "executed": True,
                    }
                )
                result_error = True
            else:
                execution_environment = {
                    **updated.public_summary(),
                    "package_operation": True,
                }
                if details.managed_command.cache_hit is not None:
                    execution_environment["cache_hit"] = details.managed_command.cache_hit
        result_payload.update(
            self._execution_environment_evidence(
                details,
                summary=execution_environment,
            )
        )
        if snapshot.sandbox_denied:
            result_payload["error"] = "sandbox_denied"
        elif snapshot.status == "exited" and snapshot.exit_code != 0:
            classified = (
                self._execution_environment_manager.classify_failure(
                    details.managed_command,
                    "\n".join(
                        str(result_payload.get(name, "")) for name in ("stdout", "stderr", "pty")
                    ),
                )
                if details.managed_command is not None
                and self._execution_environment_manager is not None
                else None
            )
            if classified is None:
                self._annotate_windows_exit(result_payload, snapshot, details)
            else:
                result_payload["error"], result_payload["message"] = classified
        elif snapshot.status != "exited":
            result_payload["error"] = snapshot.terminal_reason or snapshot.status
        elif self._looks_like_unexecuted_powershell(details.argv, result_payload):
            result_payload["error"] = "powershell_script_not_executed"
            result_payload["message"] = (
                "PowerShell 返回了脚本原文而不是执行结果，宿主已拒绝把退出码 0 当作成功。"
                "请使用 pwsh.exe 或 powershell.exe 的 -Command 单脚本参数重试。"
            )
            result_error = True
        return _result(
            result_payload,
            error=result_error,
        )

    @staticmethod
    def _execution_environment_evidence(
        details: CommandApprovalDetails,
        *,
        summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        value = details.execution_environment if summary is None else summary
        if value is None:
            return {}
        return {"execution_environment": value}

    @staticmethod
    def _looks_like_unexecuted_powershell(
        argv: tuple[str, ...],
        result_payload: dict[str, object],
    ) -> bool:
        if not argv or Path(argv[0]).name.casefold() not in _WINDOWS_POWERSHELL_EXECUTABLES:
            return False
        command_index = next(
            (
                index
                for index, value in enumerate(argv[:-1])
                if value.casefold() in {"-command", "/command", "-c"}
            ),
            None,
        )
        if command_index is None:
            return False
        script = argv[command_index + 1].strip()
        stdout = result_payload.get("stdout")
        return isinstance(stdout, str) and bool(script) and stdout.strip() == script

    @staticmethod
    def _host_path_evidence(details: CommandApprovalDetails) -> dict[str, object]:
        """Explain the boundary between host validation and sandbox observations."""

        if os.name != "nt" or details.execution != "sandboxed":
            return {}
        return {
            "host_path_validation": {
                "workdir_existed_before_start": True,
                "additional_roots_existed_before_start": True,
                "host_derived_executable_roots": [item.path for item in details.host_derived_roots],
                "sandbox_absence_proves_host_absence": False,
            },
            "sandbox_observation_note": (
                "Windows 受限沙箱中的‘找不到’或‘拒绝访问’不能证明宿主机路径、程序或进程不存在。"
                "若宿主已验证的 workdir/additional root 在命令内不可见，应报告沙箱可见性或命令"
                "兼容问题，不要报告宿主未安装。"
            ),
        }

    def _parse(
        self,
        args: dict[str, Any],
        context: ToolRunContext,
        *,
        session_id: str,
        run_id: str,
    ) -> tuple[
        CommandApprovalDetails,
        Path,
        tuple[tuple[Path, bool], ...],
        Path,
        tuple[tuple[Path, tuple[int, int, int]], ...],
    ]:
        cmd = args.get("cmd")
        raw_argv = args.get("argv")
        if (cmd is None) == (raw_argv is None):
            raise ValueError("cmd 与 argv 必须且只能提供一个")
        if cmd is not None and (
            not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_COMMAND_CHARS
        ):
            raise ValueError("cmd 无效")
        if raw_argv is not None and (
            not isinstance(raw_argv, list)
            or not 1 <= len(raw_argv) <= 256
            or any(not isinstance(item, str) or not item for item in raw_argv)
        ):
            raise ValueError("argv 无效")
        parsed_argv = cast(list[str] | None, raw_argv)
        execution = args.get("execution", "sandboxed")
        if execution not in {"sandboxed", "host_user"}:
            raise ValueError("execution 无效")
        if execution == "host_user" and raw_argv is None:
            raise CommandRuntimeError(
                "host_user_requires_argv",
                "host_user 必须使用结构化 argv，不能执行 shell 命令字符串。",
            )
        mode = args.get("mode", "buffered")
        if mode not in {"buffered", "streaming"}:
            raise ValueError("mode 无效")
        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds 无效")
        raw_workdir = args.get("workdir")
        access = args.get("access", {})
        if not isinstance(access, dict):
            raise ValueError("access 无效")
        if execution == "host_user" and access:
            raise CommandRuntimeError(
                "host_user_access_is_unrestricted",
                "host_user 不接受 access：它不受文件系统和网络沙箱限制，审批会明确展示这一点。",
            )
        filesystem = access.get("filesystem", "read_only")
        network = access.get("network", "disabled")
        if filesystem not in {"read_only", "read_write"}:
            raise ValueError("filesystem access 无效")
        if network not in {"disabled", "enabled"}:
            raise ValueError("network access 无效")
        roots = self._parse_roots(access.get("additional_roots", []))
        run_temp = self._run_temp(context, run_id)
        active_snapshot = args.get(COMMAND_SKILL_SNAPSHOT_ARG)
        active = active_snapshot if isinstance(active_snapshot, SkillTurnSnapshot) else None
        requested_workdir: Path
        display_workdir: str
        workdir: Path
        cwd_source: str
        skill_binding: CommandSkillBinding | None = None
        raw_skill = args.get("skill")
        if execution == "host_user" and raw_skill is not None:
            raise CommandRuntimeError(
                "host_user_skill_binding_unsupported",
                "Skill 脚本必须继续在 sandboxed 模式运行；host_user 只接受直接可执行文件 argv。",
            )
        if raw_skill is None:
            if raw_workdir is None:
                requested_workdir = self._session_workdir(session_id)
                cwd_source = "session_managed"
            else:
                if (
                    not isinstance(raw_workdir, str)
                    or not Path(raw_workdir).expanduser().is_absolute()
                ):
                    raise ValueError("显式 workdir 必须是绝对路径")
                requested_path = Path(raw_workdir).expanduser()
                self._reject_linked_path(requested_path)
                requested_workdir = requested_path.resolve()
                if not requested_workdir.is_dir():
                    raise CommandRuntimeError(
                        "host_path_not_found", "显式 workdir 在宿主机上不存在"
                    )
                cwd_source = "explicit"
            workdir = requested_workdir
            display_workdir = str(requested_workdir)
            self._reject_unbound_skill_paths(
                cmd if isinstance(cmd, str) else subprocess.list2cmdline(parsed_argv or ()),
                requested_workdir,
                active,
            )
        else:
            if not isinstance(raw_skill, dict):
                raise ValueError("skill 无效")
            if raw_workdir is not None:
                raise ValueError("Skill-bound 命令不能同时提供顶层 workdir")
            skill_id = raw_skill.get("skill_id")
            entry = raw_skill.get("entry")
            if not isinstance(entry, str):
                raise ValueError("skill.entry 必须是字符串")
            activations = active.activations if active is not None else ()
            if skill_id is None:
                if len(activations) == 1:
                    skill_id = activations[0].record.id
                else:
                    visible = ", ".join(item.record.id for item in activations) or "none"
                    raise CommandRuntimeError(
                        "skill_id_required",
                        "skill.skill_id is required when the current turn does not have exactly "
                        f"one activated Skill. Active Skill IDs: {visible}. If none is active, "
                        "call skill_read once for the intended Skill in this user turn before "
                        "retrying exec_command; activation from an earlier user turn is not valid.",
                    )
            if not isinstance(skill_id, str):
                raise ValueError("skill.skill_id 必须是字符串")
            relative_workdir = self._parse_skill_workdir(raw_skill.get("workdir", "."))
            activation = next(
                (item for item in activations if item.record.id == skill_id),
                None,
            )
            if activation is None:
                visible = ", ".join(item.record.id for item in activations) or "none"
                raise CommandRuntimeError(
                    "skill_not_active",
                    "skill.skill_id must be copied from the current turn's activated Skill "
                    f"<skill_id>; received {skill_id!r}. filesystem_scope_id and request_id are "
                    f"not Skill IDs. Active Skill IDs: {visible}. Do not retry this binding or use "
                    "an earlier turn's activation; call skill_read once for the intended Skill in "
                    "the current user turn, then use its successful result.",
                )
            skill_snapshot = prepare_skill_snapshot(
                context,
                activation,
                entry=entry,
                execution_root=run_temp.parent / "skills",
            )
            workdir = self._resolve_skill_workdir(relative_workdir, skill_snapshot)
            requested_workdir = workdir
            display_workdir = relative_workdir.as_posix()
            cwd_source = "skill_snapshot"
            if not isinstance(cmd, str):
                raise ValueError("Skill-bound 命令必须使用 cmd")
            cmd = self._rewrite_skill_entry(cmd, skill_snapshot)
            roots = (*roots, (skill_snapshot.snapshot_root, False))
            skill_binding = CommandSkillBinding(
                skill_id=skill_snapshot.skill_id,
                entry=skill_snapshot.entry,
                workdir=display_workdir,
                fingerprint=skill_snapshot.fingerprint,
            )
        if execution == "host_user":
            filesystem = "unrestricted"
            network = "unrestricted"
            roots = ()
        if cwd_source == "session_managed" and filesystem == "read_write":
            self._check_session_workspace_budget(workdir)
        derived_roots: tuple[tuple[Path, bool], ...]
        managed: ResolvedManagedCommand | None = None
        if parsed_argv is not None:
            semantic_argv = tuple(parsed_argv)
            managed = self._resolve_managed_command(
                semantic_argv,
                execution=execution,
                session_id=session_id,
                run_id=run_id,
            )
            argv = managed.argv if managed is not None else self._structured_argv(semantic_argv)
            if execution == "sandboxed" and Path(argv[0]).name.casefold() in {
                "powershell",
                "powershell.exe",
            }:
                raise CommandRuntimeError(
                    "windows_powershell_unsupported",
                    "系统 Windows PowerShell 5.1 在当前 Codex 沙箱中无法可靠启动；"
                    "请改用宿主 PATH 中可用的 pwsh.exe（PowerShell 7），或改写为 cmd 命令。",
                )
            display_cmd = (
                subprocess.list2cmdline(semantic_argv)
                if os.name == "nt"
                else shlex.join(semantic_argv)
            )
            derived_roots = (
                self._derive_resolved_executable_roots(Path(argv[0]), workdir, roots)
                if execution == "sandboxed" and managed is None
                else ()
            )
        else:
            assert isinstance(cmd, str)
            self._validate_windows_cmd_shape(cmd)
            direct_managed_argv = self._direct_managed_shell_argv(cmd)
            managed = (
                self._resolve_managed_command(
                    direct_managed_argv,
                    execution=execution,
                    session_id=session_id,
                    run_id=run_id,
                )
                if direct_managed_argv is not None
                else None
            )
            argv = managed.argv if managed is not None else self._shell_argv(cmd, mode)
            display_cmd = cmd
            derived_roots = (
                ()
                if managed is not None
                else self._derive_host_executable_roots(cmd, workdir, roots)
            )
        if managed is not None:
            if managed.package_operation and mode != "buffered":
                raise ManagedEnvironmentError(
                    "managed_package_requires_buffered_mode",
                    "受管 package operation 必须使用 buffered 模式，以便原子更新环境 revision。",
                )
            if managed.requires_write and filesystem != "read_write":
                raise ManagedEnvironmentError(
                    "managed_package_requires_write_access",
                    "受管 package operation 必须显式申请 filesystem=read_write。",
                )
            if managed.requires_network and network != "enabled":
                raise ManagedEnvironmentError(
                    "managed_package_requires_network_access",
                    "registry package 安装必须显式申请 network=enabled。",
                )
            roots = (
                *roots,
                *((path, False) for path in managed.read_only_roots),
                *((path, True) for path in managed.writable_roots),
            )
        roots = (*roots, *derived_roots)
        if execution == "sandboxed":
            roots = (*roots, (run_temp, True))
        derived_paths: set[Path] = {path for path, _writable in derived_roots}
        internal_paths: set[Path] = (
            {*managed.read_only_roots, *managed.writable_roots} if managed is not None else set()
        )
        root_details = tuple(
            CommandAdditionalRoot(path=str(path), access="read_write" if writable else "read_only")
            for path, writable in roots
            if path != run_temp
            and (skill_binding is None or path != skill_snapshot.snapshot_root)
            and path not in derived_paths
            and path not in internal_paths
        )
        derived_root_details = tuple(
            CommandAdditionalRoot(path=str(path), access="read_only")
            for path, _writable in derived_roots
        )
        return (
            CommandApprovalDetails(
                cmd=display_cmd,
                argv=managed.display_argv if managed is not None else argv,
                execution=cast(CommandExecution, execution),
                workdir=display_workdir,
                cwd_source=cwd_source,  # type: ignore[arg-type]
                mode=mode,
                timeout_seconds=float(timeout),
                filesystem_access=filesystem,
                additional_roots=root_details,
                host_derived_roots=derived_root_details,
                network_access=network,
                interactive=mode == "streaming",
                skill=skill_binding,
                execution_environment=(managed.public_summary() if managed is not None else None),
                launch_argv=(managed.argv if managed is not None else None),
                environment_overrides=(dict(managed.environment) if managed is not None else {}),
                managed_command=managed,
            ),
            workdir,
            roots,
            run_temp,
            tuple((path, self._path_identity(path)) for path in {workdir, *[p for p, _ in roots]}),
        )

    def _resolve_managed_command(
        self,
        argv: tuple[str, ...],
        *,
        execution: object,
        session_id: str,
        run_id: str,
    ) -> ResolvedManagedCommand | None:
        manager = self._execution_environment_manager
        if manager is None or not manager.matches_command(argv):
            return None
        if execution != "sandboxed":
            raise ManagedEnvironmentError(
                "managed_environment_requires_sandbox",
                "产品管理的执行环境只能通过 sandboxed 命令运行；显式绝对宿主程序不受此限制。",
            )
        snapshot = manager.snapshot_for_tool(session_id, run_id)
        return manager.resolve_command(snapshot, argv)

    def _direct_managed_shell_argv(self, cmd: str) -> tuple[str, ...] | None:
        manager = self._execution_environment_manager
        if manager is None:
            return None
        try:
            raw_tokens = shlex.split(cmd, posix=os.name != "nt")
        except ValueError:
            return None
        tokens = tuple(
            self._strip_windows_token_quotes(token) if os.name == "nt" else token
            for token in raw_tokens
        )
        if not tokens or not manager.matches_command(tokens):
            return None
        if any(character in cmd for character in "&|<>\r\n"):
            package_like = (
                Path(tokens[0]).name.casefold() in {"pip", "pip.exe", "pip3", "pip3.exe"}
                or (
                    Path(tokens[0]).name.casefold() in {"uv", "uv.exe"}
                    and len(tokens) > 1
                    and tokens[1].casefold() == "pip"
                )
                or (
                    Path(tokens[0]).name.casefold()
                    in {"python", "python.exe", "python3", "python3.exe"}
                    and len(tokens) > 2
                    and tokens[1:3] == ("-m", "pip")
                )
            )
            raise ManagedEnvironmentError(
                (
                    "managed_package_command_requires_direct_invocation"
                    if package_like
                    else "managed_environment_command_requires_direct_invocation"
                ),
                "包含 shell 控制符的受管命令必须改用结构化 argv，不能经 shell 猜测重写。",
            )
        return tokens

    def _derive_host_executable_roots(
        self,
        cmd: str,
        workdir: Path,
        roots: tuple[tuple[Path, bool], ...],
    ) -> tuple[tuple[Path, bool], ...]:
        """Return minimum host-derived read roots for a simple per-user PATH command."""

        if os.name != "nt":
            return ()
        direct_argv = self._direct_windows_argv(cmd, require_existing=False)
        executable_name = (
            direct_argv[0] if direct_argv is not None else self._simple_windows_executable_name(cmd)
        )
        if executable_name is None:
            return ()
        if executable_name.casefold() in _WINDOWS_CMD_BUILTINS:
            return ()
        resolved_value = shutil.which(
            executable_name,
            path=_env_value(self._base_env, "PATH"),
        )
        if not resolved_value:
            raise CommandRuntimeError(
                "executable_not_found_on_host",
                f"无法从受控宿主 PATH 解析启动程序：{executable_name}",
            )
        resolved = Path(resolved_value).resolve()
        return self._derive_resolved_executable_roots(resolved, workdir, roots)

    def _derive_resolved_executable_roots(
        self,
        resolved: Path,
        workdir: Path,
        roots: tuple[tuple[Path, bool], ...],
    ) -> tuple[tuple[Path, bool], ...]:
        if os.name != "nt":
            return ()
        user_program_roots = tuple(
            Path(value).resolve()
            for name in ("APPDATA", "LOCALAPPDATA")
            if (value := _env_value(self._base_env, name))
        )
        if not any(resolved.is_relative_to(root) for root in user_program_roots):
            return ()

        user_profile_value = _env_value(self._base_env, "USERPROFILE")
        user_profile = Path(user_profile_value).resolve() if user_profile_value else None
        approved_roots = (workdir, *[path for path, _writable in roots])
        has_narrow_scope = any(
            resolved.is_relative_to(root)
            and any(root.is_relative_to(user_root) for user_root in user_program_roots)
            for root in approved_roots
        )
        uses_broad_profile_workdir = user_profile is not None and workdir == user_profile
        if has_narrow_scope and not uses_broad_profile_workdir:
            return ()

        executable_root = resolved.parent
        self._reject_linked_path(executable_root)
        return ((executable_root, False),)

    def _session_workdir(self, session_id: str) -> Path:
        self._session_workspaces_root.mkdir(parents=True, exist_ok=True)
        workdir = session_workspace_path(self._session_workspaces_root, session_id)
        workdir.mkdir(exist_ok=True)
        self._reject_linked_path(workdir)
        return workdir.resolve()

    @staticmethod
    def _check_session_workspace_budget(workdir: Path) -> None:
        files = 0
        total_bytes = 0
        try:
            for candidate in workdir.rglob("*"):
                if not candidate.is_file():
                    continue
                files += 1
                size = candidate.stat().st_size
                total_bytes += size
                if size > MAX_SESSION_WORKSPACE_FILE_BYTES:
                    raise CommandRuntimeError(
                        "session_workspace_quota_exceeded",
                        "会话命令目录中已有文件超过单文件大小限制；新的写入请求已拒绝",
                    )
                if files > MAX_SESSION_WORKSPACE_FILES or total_bytes > MAX_SESSION_WORKSPACE_BYTES:
                    raise CommandRuntimeError(
                        "session_workspace_quota_exceeded",
                        "会话命令目录已超过容量限制；新的写入请求已拒绝",
                    )
        except OSError as exc:
            raise CommandRuntimeError(
                "session_workspace_usage_unavailable",
                "无法确认会话命令目录用量；新的写入请求已拒绝",
            ) from exc

    @staticmethod
    def _simple_windows_executable_name(cmd: str) -> str | None:
        stripped = cmd.strip()
        if not stripped or any(character in stripped for character in "&|<>\r\n"):
            return None
        if stripped.startswith('"'):
            closing = stripped.find('"', 1)
            if closing <= 1:
                return None
            token = stripped[1:closing]
        else:
            token = stripped.split(maxsplit=1)[0]
        if not token or any(character in token for character in "%$`"):
            return None
        if ":" in token or "/" in token or "\\" in token:
            return None
        return token

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
    def _parse_skill_workdir(raw: object) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("skill.workdir 必须是包内相对目录")
        relative = Path(raw)
        if relative.is_absolute() or any(part in {"..", ""} for part in relative.parts):
            raise ValueError("skill.workdir 必须是包内相对目录且不能逃逸 Skill")
        return relative

    @staticmethod
    def _resolve_skill_workdir(relative: Path, snapshot: SkillExecutionSnapshot) -> Path:
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

    def _parse_roots(self, raw: object) -> tuple[tuple[Path, bool], ...]:
        if not isinstance(raw, list) or len(raw) > 8:
            raise ValueError("additional_roots 无效")
        roots: list[tuple[Path, bool]] = []
        for item in raw:
            if not isinstance(item, dict) or item.get("access") not in {
                "read_only",
                "read_write",
            }:
                raise ValueError("additional_roots 项无效")
            locator = item.get("locator")
            path = item.get("path")
            if locator is not None and path is not None:
                raise ValueError("additional root 只能提供 path 或 locator 之一")
            if locator is not None:
                if (
                    isinstance(locator, dict)
                    and locator.get("kind") == "standard"
                    and locator.get("name") == "personal_skills"
                ):
                    requested = self._personal_skills_root
                else:
                    requested = resolve_locator(locator)
            elif isinstance(path, str) and path and Path(path).expanduser().is_absolute():
                requested = Path(path).expanduser()
            else:
                raise ValueError("additional root 需要绝对 path 或标准 locator")
            ExecCommandTool._reject_linked_path(requested)
            resolved = requested.resolve()
            if not resolved.exists():
                raise CommandRuntimeError("host_path_not_found", "additional root 在宿主机上不存在")
            roots.append((resolved, item["access"] == "read_write"))
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
            direct_argv = self._direct_windows_argv(cmd, require_existing=True)
            if direct_argv is not None:
                executable_name = Path(direct_argv[0]).name.casefold()
                if executable_name in {"pwsh", "pwsh.exe"}:
                    return direct_argv
                if executable_name in {"powershell", "powershell.exe"}:
                    raise CommandRuntimeError(
                        "windows_powershell_unsupported",
                        "系统 Windows PowerShell 5.1 在当前 Codex 沙箱中无法可靠启动；"
                        "请改用宿主 PATH 中可用的 pwsh.exe（PowerShell 7），或改写为 cmd 命令。",
                    )
                return direct_argv
            system_root = _env_value(self._base_env, "SystemRoot", "WINDIR")
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

    def _structured_argv(self, argv: tuple[str, ...]) -> tuple[str, ...]:
        executable = Path(argv[0]).expanduser()
        if executable.is_absolute():
            self._reject_linked_path(executable)
            if not executable.is_file():
                raise CommandRuntimeError(
                    "host_path_not_found",
                    "argv 中的绝对可执行文件在宿主机上不存在；请核对路径。",
                )
            return (str(executable.resolve()), *argv[1:])
        if any(separator in argv[0] for separator in ("/", "\\")):
            raise CommandRuntimeError(
                "structured_executable_must_be_absolute",
                "argv[0] 必须是绝对可执行文件路径，或宿主 PATH 中不含路径分隔符的程序名。",
            )
        resolved = shutil.which(argv[0], path=_env_value(self._base_env, "PATH"))
        if not resolved:
            raise CommandRuntimeError(
                "host_executable_not_found",
                f"宿主 PATH 中找不到 argv[0] 指定的程序：{argv[0]}",
            )
        return (str(Path(resolved).resolve()), *argv[1:])

    @staticmethod
    def _validate_windows_cmd_shape(cmd: str) -> None:
        if os.name != "nt":
            return
        stripped = cmd.lstrip()
        if stripped.startswith("&"):
            raise CommandRuntimeError(
                "powershell_syntax_in_cmd",
                "cmd 字段由 cmd.exe 解释，不能使用 PowerShell 的 & 调用运算符；"
                "请改用结构化 argv，或显式调用 pwsh.exe -Command。",
            )
        executable_match = re.match(r"^[A-Za-z]:\\[^\r\n]*?\.exe(?:\s|$)", stripped, re.I)
        if executable_match and " " in executable_match.group(0).rstrip():
            raise CommandRuntimeError(
                "windows_executable_path_requires_argv",
                "含空格的 Windows 可执行文件路径不能作为未加引号的 cmd；请使用结构化 argv。",
            )

    @staticmethod
    def _annotate_windows_exit(
        payload: dict[str, object],
        snapshot: CommandProcessSnapshot,
        details: CommandApprovalDetails,
    ) -> None:
        exit_code = snapshot.exit_code
        if os.name != "nt" or exit_code is None or exit_code >= 0:
            payload["error"] = "command_failed"
            return
        unsigned = exit_code & 0xFFFFFFFF
        code_hex = f"0x{unsigned:08X}"
        names = {
            0xC0000005: "STATUS_ACCESS_VIOLATION",
            0xC0000135: "STATUS_DLL_NOT_FOUND",
            0xC0000409: "STATUS_FAIL_FAST_EXCEPTION",
        }
        status_name = names.get(unsigned)
        payload["windows_status"] = {
            "code": code_hex,
            "name": status_name,
            "signed_exit_code": exit_code,
        }
        if unsigned == 0xC0000409:
            payload["error"] = "windows_process_fast_fail"
            if details.execution == "sandboxed":
                payload["message"] = (
                    f"进程以 Windows fast-fail 状态 {code_hex} 终止；该状态不能单独证明具体根因。"
                    "当前进程运行在受限沙箱中；若这是需要桌面用户会话的可信 GUI/宿主程序，"
                    "不要重复同一沙箱命令，应改为请求 host_user 并让用户确认。"
                )
            else:
                payload["message"] = (
                    f"进程在宿主用户模式下仍以 Windows fast-fail 状态 {code_hex} 终止；"
                    "这不是普通退出，需检查程序自身日志或依赖，不能归因于命令沙箱。"
                )
            return
        payload["error"] = "windows_process_failed"
        suffix = f" ({status_name})" if status_name else ""
        payload["message"] = (
            f"进程以 Windows 状态 {code_hex}{suffix} 终止；这是异常终止码，不是普通正数退出码。"
        )

    def _direct_windows_argv(
        self,
        cmd: str,
        *,
        require_existing: bool,
    ) -> tuple[str, ...] | None:
        """Bypass cmd.exe for explicit PowerShell and quoted absolute executables.

        ``codex exec-server`` already accepts an argv vector. Passing a second shell's
        quoted command through ``cmd.exe /C`` makes Windows apply two incompatible quote
        parsers and can turn ``-Command \"script\"`` into a successful string echo.
        """

        try:
            raw_tokens = shlex.split(cmd, posix=False)
        except ValueError:
            return None
        tokens = tuple(self._strip_windows_token_quotes(token) for token in raw_tokens)
        if not tokens:
            return None
        executable = Path(tokens[0])
        executable_name = executable.name.casefold()
        is_powershell = executable_name in _WINDOWS_POWERSHELL_EXECUTABLES
        is_quoted_absolute_executable = (
            cmd.lstrip().startswith(('"', "'"))
            and executable.is_absolute()
            and executable.suffix.casefold() in {".exe", ".com"}
            and not any(token in _WINDOWS_SHELL_OPERATORS for token in tokens[1:])
        )
        if not is_powershell and not is_quoted_absolute_executable:
            return None

        resolved = shutil.which(tokens[0], path=_env_value(self._base_env, "PATH"))
        if resolved:
            return (str(Path(resolved).resolve()), *tokens[1:])
        if executable.is_absolute():
            if require_existing and not executable.is_file():
                raise CommandRuntimeError(
                    "host_path_not_found",
                    "命令中的绝对可执行文件在宿主机上不存在；请核对路径，不能根据沙箱结果猜测。",
                )
            return tokens
        if require_existing:
            raise CommandRuntimeError(
                "host_executable_not_found",
                f"宿主 PATH 中找不到显式请求的 {tokens[0]}；请改用实际可用的 PowerShell 可执行文件。",
            )
        return tokens

    @staticmethod
    def _strip_windows_token_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

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

    def _child_env(
        self,
        run_temp: Path,
        *,
        network_enabled: bool = False,
        overrides: dict[str, str] | None = None,
    ) -> dict[str, str]:
        canonical_names = {
            "PATH": "PATH",
            "PATHEXT": "PATHEXT",
            "SYSTEMROOT": "SystemRoot",
            "SYSTEMDRIVE": "SystemDrive",
            "WINDIR": "WINDIR",
            "COMSPEC": "ComSpec",
            "LANG": "LANG",
            "LC_ALL": "LC_ALL",
            "TMP": "TMP",
            "TEMP": "TEMP",
            "TMPDIR": "TMPDIR",
            "HOME": "HOME",
            "USERPROFILE": "USERPROFILE",
            "HOMEDRIVE": "HOMEDRIVE",
            "HOMEPATH": "HOMEPATH",
            "APPDATA": "APPDATA",
            "LOCALAPPDATA": "LOCALAPPDATA",
        }
        casefolded = {key.upper(): value for key, value in self._base_env.items()}
        env = {
            canonical: casefolded[key]
            for key, canonical in canonical_names.items()
            if key in casefolded
        }
        managed_overrides = dict(overrides or {})
        managed_path = managed_overrides.pop("PATH", None)
        env.update(managed_overrides)
        if managed_path is not None:
            existing_path = env.get("PATH", "")
            env["PATH"] = (
                os.pathsep.join((managed_path, existing_path)) if existing_path else managed_path
            )
        env.update(
            {
                "TEMP": str(run_temp),
                "TMP": str(run_temp),
                "TMPDIR": str(run_temp),
                "AGENT_FRIEND_PERSONAL_SKILLS_DIR": str(self._personal_skills_root),
            }
        )
        if network_enabled:
            env.update(safe_loopback_proxy_environment(self._base_env))
        return env

    def _host_user_env(self) -> dict[str, str]:
        """Preserve the current desktop user's normal environment for approved host execution."""

        return dict(self._base_env)

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
            "process_id": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "当前 run 中 exec_command(mode=streaming) 成功结果返回的 process_id；"
                    "不能跨 run、会话或进程复用。"
                ),
            },
            "chars": {
                "type": "string",
                "maxLength": MAX_STDIN_BYTES,
                "description": "确实需要交互输入时写入进程的字符；只等待状态时省略。",
            },
            "wait_for": {
                "type": "string",
                "enum": ["none", "output", "exit"],
                "default": "none",
                "description": "不等待、等待 after_seq 之后的新输出，或等待进程退出。",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0,
                "maximum": 60,
                "default": 0,
                "description": "本次等待上限秒数；不要用短间隔高频轮询。",
            },
            "after_seq": {
                "type": "integer",
                "minimum": 0,
                "description": "上次结果中的 output_seq；wait_for=output 时只等待其后的新输出。",
            },
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
    execution_environment_manager: ExecutionEnvironmentManager | None = None,
) -> list[Tool]:
    return [
        ExecCommandTool(
            runtime,
            broker,
            execution_environment_manager=execution_environment_manager,
        ),
        WriteStdinTool(runtime),
    ]
