"""Frozen notification facts and the desktop projection."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

NotificationKind = Literal[
    "reply_ready", "needs_action", "run_failed", "run_interrupted", "todo_due", "ambient"
]


@dataclass(frozen=True)
class NotificationFact:
    event_key: str
    kind: NotificationKind
    title: str
    persona_id: str
    session_id: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    occurrence_id: str | None = None
    owner_user_id: str = "local"
    task: str = ""
    detail: str = ""
    reply: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_key or not self.persona_id or not self.owner_user_id:
            raise ValueError("notification identity and owner must be present")
        if self.created_at.tzinfo is None or (
            self.expires_at is not None and self.expires_at.tzinfo is None
        ):
            raise ValueError("notification timestamps must be timezone-aware")

    @property
    def notification_id(self) -> str:
        key = f"{self.owner_user_id}\n{self.event_key}".encode()
        return hashlib.sha256(key).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["created_at"] = self.created_at.astimezone(UTC).isoformat()
        value["expires_at"] = (
            (self.expires_at or self.created_at + timedelta(hours=24)).astimezone(UTC).isoformat()
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NotificationFact:
        data = dict(value)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        return cls(**data)


def fallback_text(fact: NotificationFact) -> str:
    title = fact.title[:64] or "对话"
    if fact.kind == "todo_due":
        return f"提醒你一下：{title}，已经到计划时间了。"
    if fact.kind == "needs_action":
        return f"「{title}」有一项操作需要你确认，方便时来看一下。"
    if fact.kind == "run_failed":
        return f"「{title}」这轮遇到了问题，回来看看具体情况吧。"
    if fact.kind == "run_interrupted":
        return f"「{title}」这轮意外中断了，回来看看吧。"
    return f"「{title}」有新回复了，方便时回来看看吧。"


@dataclass(frozen=True)
class NotificationRecord:
    fact: NotificationFact
    generation_status: str
    text: str
    text_source: str | None
    version: int
    delivery: dict[str, Any]

    @property
    def notification_id(self) -> str:
        return self.fact.notification_id

    def to_wire(self) -> dict[str, Any]:
        fact = self.fact
        return {
            "notificationId": self.notification_id,
            "eventKey": fact.event_key,
            "kind": fact.kind,
            "sessionId": fact.session_id,
            "runId": fact.run_id,
            "approvalId": fact.approval_id,
            "occurrenceId": fact.occurrence_id,
            "title": fact.title,
            "text": self.text,
            "generationStatus": self.generation_status,
            "textSource": self.text_source,
            "version": self.version,
            "createdAt": fact.to_dict()["created_at"],
            "expiresAt": fact.to_dict()["expires_at"],
            "delivery": {
                key: self.delivery[key]
                for key in (
                    "channel",
                    "status",
                    "attempt",
                    "presentedAt",
                    "presentedText",
                    "truncated",
                    "dismissedAt",
                )
            },
            "navigation": {"sessionId": fact.session_id} if fact.session_id else None,
        }
