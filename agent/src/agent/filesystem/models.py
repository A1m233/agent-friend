"""Turn-scoped filesystem capabilities and approval payloads."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from ..approvals.models import (
    ApprovalDecisionSource,
    ApprovalLifetime,
    ApprovalStatus,
    RiskAssessment,
    SessionApprovalMode,
)

FilesystemPermission = Literal["search_metadata", "read_text", "read_image", "create", "edit"]
DestructiveAction = Literal["overwrite", "move", "delete"]
FilesystemApprovalStatus = ApprovalStatus


@dataclass(frozen=True)
class FilesystemCapability:
    scope_id: str
    root: Path
    root_kind: Literal["file", "directory"]
    permissions: frozenset[FilesystemPermission]
    allow_protected: bool = False
    source: Literal["user", "skill"] = "user"
    label: str | None = None


@dataclass(frozen=True)
class DestructiveActionGrant:
    action_id: str
    action: DestructiveAction
    target: Path
    recursive: bool = False
    expected_identity: str | None = None
    risk_fingerprint: str | None = None
    action_fingerprint: str = ""


@dataclass(frozen=True)
class ApprovalItem:
    item_id: str
    path: Path
    root_kind: Literal["file", "directory"]
    permissions: frozenset[FilesystemPermission]
    allow_protected: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "path": str(self.path),
            "root_kind": self.root_kind,
            "permissions": sorted(self.permissions),
        }


@dataclass(frozen=True)
class ApprovalAction:
    action_id: str
    action: DestructiveAction
    target: Path
    recursive: bool = False
    expected_identity: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "target": str(self.target),
            "recursive": self.recursive,
        }


@dataclass(frozen=True)
class ApprovalDecision:
    approved_permissions: dict[str, frozenset[FilesystemPermission]] = field(default_factory=dict)
    approved_action_ids: frozenset[str] = frozenset()

    @property
    def approved(self) -> bool:
        return bool(self.approved_permissions or self.approved_action_ids)


@dataclass
class PendingFilesystemApproval:
    request_id: str
    session_id: str
    run_id: str
    tool_call_id: str
    reason: str
    items: tuple[ApprovalItem, ...]
    actions: tuple[ApprovalAction, ...]
    kind: str = "filesystem"
    requested_lifetime: ApprovalLifetime = "once"
    version: int = 1
    status: FilesystemApprovalStatus = "pending"
    approval_mode: SessionApprovalMode = "per_request"
    approval_mode_version: int = 0
    decision_source: ApprovalDecisionSource | None = None
    risk_assessment: RiskAssessment | None = None
    _decision: ApprovalDecision | None = field(default=None, repr=False)
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        run_id: str,
        tool_call_id: str,
        reason: str,
        items: tuple[ApprovalItem, ...],
        actions: tuple[ApprovalAction, ...],
    ) -> PendingFilesystemApproval:
        return cls(
            request_id=f"fsreq_{uuid4().hex}",
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            reason=reason,
            items=items,
            actions=actions,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "reason": self.reason,
            "items": [item.to_dict() for item in self.items],
            "actions": [action.to_dict() for action in self.actions],
            "requested_lifetime": self.requested_lifetime,
            "version": self.version,
            "status": self.status,
            "approval_mode": self.approval_mode,
            "approval_mode_version": self.approval_mode_version,
            "decision_source": self.decision_source,
            "risk": (self.risk_assessment.to_dict() if self.risk_assessment is not None else None),
        }

    @staticmethod
    def cancelled_decision() -> ApprovalDecision:
        return ApprovalDecision()

    def fully_approved_decision(self) -> ApprovalDecision:
        return ApprovalDecision(
            approved_permissions={item.item_id: item.permissions for item in self.items},
            approved_action_ids=frozenset(action.action_id for action in self.actions),
        )


class FilesystemAuthorizationLedger:
    """Capabilities valid only for the lifetime of one ``ToolRunContext``."""

    def __init__(self) -> None:
        self._scopes: dict[str, FilesystemCapability] = {}
        self._actions: dict[str, DestructiveActionGrant] = {}
        self._consumed_actions: set[str] = set()

    def grant_scope(
        self,
        *,
        root: Path,
        root_kind: Literal["file", "directory"],
        permissions: frozenset[FilesystemPermission],
        allow_protected: bool = False,
        source: Literal["user", "skill"] = "user",
        label: str | None = None,
    ) -> FilesystemCapability:
        capability = FilesystemCapability(
            scope_id=f"scope_{uuid4().hex}",
            root=root,
            root_kind=root_kind,
            permissions=permissions,
            allow_protected=allow_protected,
            source=source,
            label=label,
        )
        self._scopes[capability.scope_id] = capability
        return capability

    def grant_action(
        self,
        action: ApprovalAction,
        *,
        risk_fingerprint: str | None = None,
    ) -> DestructiveActionGrant:
        grant = DestructiveActionGrant(
            action_id=action.action_id,
            action=action.action,
            target=action.target,
            recursive=action.recursive,
            expected_identity=action.expected_identity,
            risk_fingerprint=risk_fingerprint,
            action_fingerprint=_destructive_action_fingerprint(
                action.action,
                action.target,
                action.recursive,
                action.expected_identity,
            ),
        )
        self._actions[grant.action_id] = grant
        return grant

    def scope(self, scope_id: str) -> FilesystemCapability | None:
        return self._scopes.get(scope_id)

    def consume_action(
        self,
        action_id: str,
        *,
        action: DestructiveAction,
        target: Path,
        recursive: bool = False,
    ) -> bool:
        return (
            self.consume_action_status(
                action_id,
                action=action,
                target=target,
                recursive=recursive,
            )
            == "ok"
        )

    def consume_action_status(
        self,
        action_id: str,
        *,
        action: DestructiveAction,
        target: Path,
        recursive: bool = False,
    ) -> Literal["ok", "missing", "target_changed"]:
        grant = self._actions.get(action_id)
        if grant is None or action_id in self._consumed_actions:
            return "missing"
        if (
            grant.action != action
            or grant.target != target
            or (action == "delete" and grant.recursive != recursive)
            or grant.action_fingerprint
            != _destructive_action_fingerprint(
                action,
                target,
                recursive,
                grant.expected_identity,
            )
        ):
            return "missing"
        if grant.expected_identity is not None:
            try:
                info = target.lstat()
            except OSError:
                return "target_changed"
            identity = f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
            if identity != grant.expected_identity:
                return "target_changed"
        self._consumed_actions.add(action_id)
        return "ok"

    def scopes(self) -> tuple[FilesystemCapability, ...]:
        return tuple(self._scopes.values())


def _destructive_action_fingerprint(
    action: DestructiveAction,
    target: Path,
    recursive: bool,
    expected_identity: str | None,
) -> str:
    value = "\0".join((action, str(target), str(recursive), expected_identity or ""))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
