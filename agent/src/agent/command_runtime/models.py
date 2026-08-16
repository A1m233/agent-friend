"""Typed approval and process state for governed commands."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from ..approvals.models import ApprovalLifetime, ApprovalStatus

CommandMode = Literal["buffered", "streaming"]
FilesystemAccess = Literal["read_only", "workspace_write"]
NetworkAccess = Literal["disabled", "enabled"]
CommandProcessStatus = Literal["running", "exited", "cancelled", "timed_out", "runtime_lost"]


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
    fingerprint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "entry": self.entry,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class CommandApprovalDetails:
    cmd: str
    argv: tuple[str, ...]
    workdir: str
    mode: CommandMode
    timeout_seconds: float
    filesystem_access: FilesystemAccess
    additional_roots: tuple[CommandAdditionalRoot, ...]
    network_access: NetworkAccess
    interactive: bool
    skill: CommandSkillBinding | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "cmd": self.cmd,
            "argv": list(self.argv),
            "workdir": self.workdir,
            "mode": self.mode,
            "timeout_seconds": self.timeout_seconds,
            "filesystem_access": self.filesystem_access,
            "additional_roots": [item.to_dict() for item in self.additional_roots],
            "network_access": self.network_access,
            "interactive": self.interactive,
            "skill": self.skill.to_dict() if self.skill is not None else None,
        }


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
        }

    @staticmethod
    def cancelled_decision() -> CommandApprovalDecision:
        return CommandApprovalDecision(allow=False)


@dataclass
class CommandProcessSnapshot:
    process_id: str
    session_id: str
    run_id: str
    tool_call_id: str
    mode: CommandMode
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
            "mode": self.mode,
            "status": self.status,
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
                    "stdout": self.stdout_tail.decode("utf-8", errors="replace"),
                    "stderr": self.stderr_tail.decode("utf-8", errors="replace"),
                    "pty": self.pty_tail.decode("utf-8", errors="replace"),
                }
            )
        return value
