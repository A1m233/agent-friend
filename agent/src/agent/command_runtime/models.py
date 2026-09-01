"""Typed approval and process state for governed commands."""

from __future__ import annotations

import locale
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ..approvals.models import (
    ApprovalDecisionSource,
    ApprovalLifetime,
    ApprovalStatus,
    RiskAssessment,
    SessionApprovalMode,
)

if TYPE_CHECKING:
    from ..execution_environment import ResolvedManagedCommand

CommandMode = Literal["buffered", "streaming"]
CommandExecution = Literal["sandboxed", "host_user"]
FilesystemAccess = Literal["read_only", "read_write", "unrestricted"]
CwdSource = Literal["session_managed", "explicit", "skill_snapshot"]
NetworkAccess = Literal["disabled", "enabled", "unrestricted"]
CommandProcessStatus = Literal["running", "exited", "cancelled", "timed_out", "runtime_lost"]


def decode_process_output(value: bytes, *, fallback_encoding: str | None = None) -> str:
    """Decode raw exec-server bytes without assuming every Windows command emits UTF-8."""

    if value.startswith((b"\xff\xfe", b"\xfe\xff")) or value.count(b"\x00") > len(value) // 4:
        return value.decode("utf-16", errors="replace")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        # The desktop supervisor deliberately starts Python in UTF-8 mode so
        # bridge I/O is stable.  getpreferredencoding() follows that mode and
        # therefore cannot tell us which legacy Windows code page cmd.exe used
        # for captured output.  getencoding() reports the actual locale
        # encoding independently of Python UTF-8 mode.
        encoding = fallback_encoding or locale.getencoding()
        if encoding.lower().replace("-", "") in {"utf8", "utf_8"}:
            return value.decode("utf-8", errors="replace")
        return value.decode(encoding, errors="replace")


@dataclass(frozen=True)
class CommandAdditionalRoot:
    path: str
    access: FilesystemAccess

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "access": self.access}


@dataclass(frozen=True)
class CommandSkillBinding:
    skill_id: str
    entry: str
    workdir: str
    fingerprint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "entry": self.entry,
            "workdir": self.workdir,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class CommandApprovalDetails:
    cmd: str
    argv: tuple[str, ...]
    workdir: str
    cwd_source: CwdSource
    mode: CommandMode
    timeout_seconds: float
    filesystem_access: FilesystemAccess
    additional_roots: tuple[CommandAdditionalRoot, ...]
    host_derived_roots: tuple[CommandAdditionalRoot, ...]
    network_access: NetworkAccess
    interactive: bool
    execution: CommandExecution = "sandboxed"
    skill: CommandSkillBinding | None = None
    execution_environment: dict[str, object] | None = None
    launch_argv: tuple[str, ...] | None = field(default=None, repr=False, compare=False)
    launch_workdir: str | None = field(default=None, repr=False, compare=False)
    environment_overrides: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    managed_command: ResolvedManagedCommand | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "cmd": self.cmd,
            "argv": list(self.argv),
            "execution": self.execution,
            "workdir": self.workdir,
            "cwd_source": self.cwd_source,
            "mode": self.mode,
            "timeout_seconds": self.timeout_seconds,
            "filesystem_access": self.filesystem_access,
            "additional_roots": [item.to_dict() for item in self.additional_roots],
            "host_derived_roots": [item.to_dict() for item in self.host_derived_roots],
            "network_access": self.network_access,
            "interactive": self.interactive,
            "skill": self.skill.to_dict() if self.skill is not None else None,
        }
        if self.execution_environment is not None:
            result["execution_environment"] = self.execution_environment
        return result


@dataclass(frozen=True)
class CommandApprovalDecision:
    allow: bool
    lifetime: ApprovalLifetime = "once"

    @property
    def approved(self) -> bool:
        return self.allow and self.lifetime == "once"


@dataclass
class PendingCommandApproval:
    request_id: str
    session_id: str
    run_id: str
    tool_call_id: str
    reason: str
    details: CommandApprovalDetails
    kind: str = "command"
    requested_lifetime: ApprovalLifetime = "once"
    version: int = 1
    status: ApprovalStatus = "pending"
    approval_mode: SessionApprovalMode = "per_request"
    approval_mode_version: int = 0
    decision_source: ApprovalDecisionSource | None = None
    risk_assessment: RiskAssessment | None = None
    _decision: CommandApprovalDecision | None = field(default=None, repr=False)
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        run_id: str,
        tool_call_id: str,
        reason: str,
        details: CommandApprovalDetails,
    ) -> PendingCommandApproval:
        return cls(
            request_id=f"cmdreq_{uuid4().hex}",
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            reason=reason,
            details=details,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "reason": self.reason,
            "details": self.details.to_dict(),
            "requested_lifetime": self.requested_lifetime,
            "version": self.version,
            "status": self.status,
            "approval_mode": self.approval_mode,
            "approval_mode_version": self.approval_mode_version,
            "decision_source": self.decision_source,
            "risk": (self.risk_assessment.to_dict() if self.risk_assessment is not None else None),
        }

    @staticmethod
    def cancelled_decision() -> CommandApprovalDecision:
        return CommandApprovalDecision(allow=False)

    @staticmethod
    def fully_approved_decision() -> CommandApprovalDecision:
        return CommandApprovalDecision(allow=True, lifetime="once")


@dataclass
class CommandProcessSnapshot:
    process_id: str
    session_id: str
    run_id: str
    tool_call_id: str
    mode: CommandMode
    tool_name: str = "exec_command"
    evidence_ref: str | None = None
    recovery_mode: str = "resident_only"
    runtime_instance_id: str = ""
    status: CommandProcessStatus = "running"
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    duration_seconds: float | None = None
    output_seq: int = 0
    stdout_tail: bytes = b""
    stderr_tail: bytes = b""
    pty_tail: bytes = b""
    dropped_bytes: int = 0
    exit_code: int | None = None
    sandbox_denied: bool = False
    terminal_reason: str | None = None
    sandbox_type: str | None = None
    version: int = 1

    def terminal(self) -> bool:
        return self.status != "running"

    def to_dict(self, *, include_output: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "process_id": self.process_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "mode": self.mode,
            "recovery_mode": self.recovery_mode,
            "runtime_instance_id": self.runtime_instance_id,
            "status": self.status,
            "process_status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "output_seq": self.output_seq,
            "dropped_bytes": self.dropped_bytes,
            "exit_code": self.exit_code,
            "sandbox_denied": self.sandbox_denied,
            "terminal_reason": self.terminal_reason,
            "sandbox_type": self.sandbox_type,
            "version": self.version,
        }
        if include_output:
            value.update(
                {
                    "stdout": decode_process_output(self.stdout_tail),
                    "stderr": decode_process_output(self.stderr_tail),
                    "pty": decode_process_output(self.pty_tail),
                }
            )
        return value
