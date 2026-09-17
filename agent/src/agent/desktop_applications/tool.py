"""Desktop application launch tool and durable at-most-once transaction boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from ..approvals import ApprovalBroker
from ..command_runtime.models import CommandApprovalDetails
from ..command_runtime.tools import (
    COMMAND_EXECUTION_CHECKPOINT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
)
from ..sessions.tool_execution_evidence import (
    ToolExecutionEvidence,
    ToolExecutionEvidenceError,
    ToolExecutionEvidenceStore,
)
from ..tool_runtime.control import (
    TOOL_INVOCATION_CONTROL_ARG,
    ToolInvocationCancelled,
    ToolInvocationControl,
)
from ..tools.protocol import ToolResult
from .launcher import NativeApplicationLauncher
from .models import PendingApplicationApproval

logger = logging.getLogger(__name__)


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    if path.is_dir():
        return stat.st_dev, stat.st_ino, 0, 0
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class LaunchDesktopApplicationTool:
    supports_invocation_control: ClassVar[bool] = True
    timeout_seconds: ClassVar[float] = 330
    name: ClassVar[str] = "launch_desktop_application"
    description: ClassVar[str] = (
        "通过当前 Windows 用户桌面正常打开一个已确认的本地 exe 应用。用于任务结束后用户仍需使用的"
        "桌面应用；应用不随任务完成、取消或本产品退出被清理。需要等待退出、读取输出或交互输入的"
        "命令使用 exec_command/host_process_start，不用本工具运行常驻脚本。先检查既有目标实例，"
        "已有可用实例则复用。路径和参数必须来自用户或本机发现，不凭应用名猜测。"
        "启动受现有审批模式约束且不受命令沙箱限制。accepted 只证明系统接受打开请求，不证明"
        "窗口、设备或业务已就绪，必须另行核验；unknown 可能已经打开，不得直接重复启动。"
        "launch_id 仅供证据关联，不是 process_id，不可用于 write_stdin 或 terminate_command。"
        "不支持快捷方式、URI、非 Windows 或没有当前交互桌面的入口；不可用时报告缺口，不绕行。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "executable_path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 32767,
                "description": "来自用户或本机发现的既存本地 exe 绝对路径，不能填应用名称、URI、快捷方式或命令字符串。",
            },
            "arguments": {
                "type": "array",
                "maxItems": 128,
                "items": {"type": "string", "maxLength": 30000},
                "description": "目标应用的独立参数数组，省略时为空；无需 shell 引号，不接受整段 shell 命令。",
            },
            "working_directory": {
                "type": "string",
                "minLength": 1,
                "maxLength": 32767,
                "description": "既存绝对目录；省略时使用目标 exe 所在目录。",
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": "向用户说明本次打开应用的用途。",
            },
        },
        "required": ["executable_path", "reason"],
    }

    def __init__(
        self,
        launcher: NativeApplicationLauncher,
        broker: ApprovalBroker | None,
        evidence_store: ToolExecutionEvidenceStore,
    ) -> None:
        self._launcher = launcher
        self._broker = broker
        self._evidence_store = evidence_store
        self._lock = threading.RLock()
        self._transactions: dict[str, tuple[Any, int]] = {}

    @contextmanager
    def _transaction(self, key: str) -> Iterator[None]:
        with self._lock:
            lock, users = self._transactions.get(key, (threading.RLock(), 0))
            self._transactions[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._lock:
                _, users = self._transactions[key]
                if users == 1:
                    del self._transactions[key]
                else:
                    self._transactions[key] = (lock, users - 1)

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        if args.get(COMMAND_SURFACE_ARG) != "desktop_chat" or self._broker is None:
            return self._result({"status": "failed", "error_code": "approval_surface_unavailable"})
        owners = tuple(
            args.get(key)
            for key in (COMMAND_SESSION_ID_ARG, COMMAND_RUN_ID_ARG, COMMAND_TOOL_CALL_ID_ARG)
        )
        if not all(isinstance(v, str) and v for v in owners):
            return self._result({"status": "failed", "error_code": "missing_execution_identity"})
        session_id, run_id, call_id = (str(v) for v in owners)
        digest = hashlib.sha256(json.dumps([session_id, run_id, call_id]).encode()).hexdigest()[:32]
        reference, launch_id = f"tev_{digest}", f"launch_{digest}"
        control = args.get(TOOL_INVOCATION_CONTROL_ARG)
        if not isinstance(control, ToolInvocationControl):
            control = ToolInvocationControl()
        with self._transaction(reference):
            try:
                prior = self._evidence_store.load(session_id, reference)
            except ToolExecutionEvidenceError:
                return self._result(
                    {"status": "unknown", "error_code": "launch_evidence_unavailable"}
                )
            if prior is not None:
                try:
                    result = json.loads(prior.stdout)
                    if not isinstance(result, dict) or prior.kind != "application_launch":
                        raise ValueError("invalid launch evidence")
                except ValueError:
                    return self._result(
                        {"status": "unknown", "error_code": "launch_evidence_unavailable"}
                    )
                if result.get("status") in {"prepared", "dispatching"}:
                    result.update(status="unknown", error_code="launch_outcome_unknown")
                return self._result(result)
            error = self._launcher.validate()
            if error:
                return self._result({"status": "failed", "error_code": error})
            try:
                executable = Path(args["executable_path"])
                if (
                    not executable.is_absolute()
                    or not executable.is_file()
                    or executable.suffix.lower() != ".exe"
                    or str(executable).startswith("\\\\")
                ):
                    raise ValueError("executable_path 必须是既存本地 exe 绝对路径。")
                executable = executable.resolve(strict=True)
                if str(executable).startswith("\\\\"):
                    raise ValueError("executable_path 必须是本地 exe。")
                arguments = args.get("arguments", [])
                if (
                    not isinstance(arguments, list)
                    or len(arguments) > 128
                    or any(not isinstance(v, str) or "\0" in v for v in arguments)
                ):
                    raise ValueError("arguments 必须是独立字符串数组且不含 NUL。")
                if len(subprocess.list2cmdline(arguments).encode("utf-16-le")) > 60000:
                    raise ValueError("arguments 超过 Windows 启动参数上限。")
                workdir = Path(args.get("working_directory", str(executable.parent)))
                if not workdir.is_absolute() or not workdir.is_dir():
                    raise ValueError("working_directory 必须是既存绝对目录。")
                workdir = workdir.resolve(strict=True)
                reason = args.get("reason")
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
                    raise ValueError("reason 必须说明本次打开应用的用途，最多 300 字。")
                identity = (_file_identity(executable), _file_identity(workdir))
                control.check_cancelled()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                return self._result(
                    {"status": "failed", "error_code": "invalid_launch_target", "message": str(exc)}
                )
            details = CommandApprovalDetails(
                cmd="",
                argv=(str(executable), *arguments),
                workdir=str(workdir),
                cwd_source="explicit",
                mode="buffered",
                timeout_seconds=12,
                filesystem_access="unrestricted",
                additional_roots=(),
                host_derived_roots=(),
                network_access="unrestricted",
                interactive=False,
                execution="host_user",
            )
            request = PendingApplicationApproval.create(
                session_id=session_id,
                run_id=run_id,
                tool_call_id=call_id,
                reason=reason,
                details=details,
            )
            broker = self._broker
            token = control.add_cancel_callback(lambda _: broker.cancel_run(session_id, run_id))
            dispatched = False
            try:
                control.check_cancelled()
                decision = self._broker.request(request, timeout_seconds=300)
            finally:
                control.remove_cancel_callback(token)
            if decision is None or not decision.approved:
                return self._result(
                    {
                        "status": "cancelled" if request.status == "cancelled" else "denied",
                        "error_code": f"approval_{request.status}",
                    }
                )

            def persist(result: dict[str, Any]) -> None:
                self._evidence_store.write(
                    ToolExecutionEvidence(
                        evidence_ref=reference,
                        session_id=session_id,
                        run_id=run_id,
                        tool_call_id=call_id,
                        operation_id=launch_id,
                        tool_name=self.name,
                        process_status=str(result["status"]),
                        output_seq=1,
                        dropped_bytes=0,
                        stdout=json.dumps(result, ensure_ascii=False),
                        source="desktop_application_launcher",
                        kind="application_launch",
                        captured_at=datetime.now(UTC).isoformat(),
                    )
                )

            try:
                control.check_cancelled()
                if identity != (_file_identity(executable), _file_identity(workdir)):
                    return self._result(
                        {"status": "failed", "error_code": "approval_target_changed"}
                    )
                risk = self._broker.reassess(request)
                if (
                    request.risk_assessment
                    and risk
                    and risk.fingerprint != request.risk_assessment.fingerprint
                ):
                    return self._result(
                        {"status": "failed", "error_code": "approval_target_changed"}
                    )
                base = {
                    "launch_id": launch_id,
                    "evidence_ref": reference,
                    "lifecycle": "user_desktop",
                    "readiness": "not_verified",
                    "approval_source": request.decision_source,
                    "target_fingerprint": hashlib.sha256(
                        json.dumps(
                            [str(executable), arguments, str(workdir), identity],
                            ensure_ascii=False,
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                checkpoint = args.get(COMMAND_EXECUTION_CHECKPOINT_ARG)
                if not callable(checkpoint):
                    return self._result(
                        {"status": "failed", "error_code": "checkpoint_unavailable"}
                    )
                persist({**base, "status": "prepared"})
                checkpoint(launch_id, "persisted_evidence", reference)
                control.check_cancelled()
                persist({**base, "status": "dispatching"})
                dispatched = True
                result = {
                    **base,
                    **self._launcher.launch(
                        {
                            "executable_path": str(executable),
                            "arguments": arguments,
                            "working_directory": str(workdir),
                        },
                        control,
                    ),
                }
            except ToolInvocationCancelled:
                result = {
                    "launch_id": launch_id,
                    "evidence_ref": reference,
                    "status": "cancelled",
                    "error_code": "cancelled_before_dispatch",
                    "readiness": "not_verified",
                }
            except (OSError, ToolExecutionEvidenceError):
                return self._result(
                    {
                        "status": "unknown" if dispatched else "failed",
                        "error_code": "launch_evidence_unavailable",
                    }
                )
            try:
                persist(result)
            except (OSError, ToolExecutionEvidenceError):
                return self._result(
                    {
                        "status": "unknown",
                        "error_code": "launch_evidence_unavailable",
                        "launch_id": launch_id,
                        "evidence_ref": reference,
                    }
                )
            return self._result(result)

    @staticmethod
    def _result(value: dict[str, Any]) -> ToolResult:
        status = value.get("status")
        value.setdefault(
            "message",
            "系统已接受打开请求；请独立核验窗口及业务状态。"
            if status == "accepted"
            else "启动结果未知，应用可能已打开；先检查现有实例，不要直接重复启动。"
            if status == "unknown"
            else "本次未确认打开成功；检查目标、平台或授权状态，不要绕过该限制。",
        )
        return ToolResult(
            json.dumps(value, ensure_ascii=False),
            is_error=status != "accepted",
            meta={
                "executed": "unknown" if status == "unknown" else status == "accepted",
                "evidence_ref": value.get("evidence_ref"),
            },
        )
