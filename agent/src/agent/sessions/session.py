"""``Session`` —— 会话聚合根。

承载会话元数据 + 事件流；**不负责持久化**（IO 由 :class:`SessionStore` 完成），
**不负责对话执行**（由 :class:`Conversation` 完成）。

详见 docs/requirements/002-engine-session-management/design.md §4.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..messages import Message
from .errors import SessionCorruptError
from .events import SCHEMA_VERSION, Event, _ensure_utc, _parse_ts, _serialize_ts


def _new_session_id() -> str:
    """生成新 session_id（uuid4 字符串）。"""
    return str(uuid4())


@dataclass
class Session:
    """会话聚合根。

    Attributes:
        session_id: uuid4 字符串。
        created_at: 来自首行 ``session_meta`` 事件的 ts，作为会话创建时间。
        initial_title: 创建时使用的标题（**不可变**：list 视图永远显示这个）。
        initial_persona: 创建时使用的 persona 名。
        initial_model: 创建时使用的 model 名。
        events: 完整事件流（含首行 ``session_meta``）。仅在内存里；IO 由
            :class:`SessionStore` 负责，**禁止外部直接 append**——应通过
            :meth:`append` 配合 store 落盘。

    Note:
        ``current_persona`` / ``current_model`` / ``messages`` 都是 property，
        从 :attr:`events` **派生**（单一真相源）。事件流是 append-only，
        所以这些派生属性永远与文件状态一致。
    """

    session_id: str
    created_at: datetime
    initial_title: str
    initial_persona: str
    initial_model: str
    events: list[Event] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        title: str,
        persona: str,
        model: str,
        *,
        session_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Session:
        """创建一个**全新**的 :class:`Session`，并自动生成首行 ``session_meta`` 事件。

        Args:
            title: 初始标题。
            persona: 初始 persona 名。
            model: 初始 model 名（LiteLLM 风格，如 ``"deepseek/deepseek-chat"``）。
            session_id: 可选；不传则自动 uuid4。
            created_at: 可选；不传则用 :func:`datetime.now` UTC 当前时间。

        Returns:
            包含一条 ``session_meta`` 事件的新 Session 实例。

        Note:
            返回后**还没落盘**——调用方应紧接着 ``store.create(session)`` 写文件。
        """
        sid = session_id or _new_session_id()
        ts = _ensure_utc(created_at) if created_at else datetime.now(UTC)
        meta_event = Event(
            type="session_meta",
            uuid=sid,
            ts=ts,
            payload={
                "schema_version": SCHEMA_VERSION,
                "initial_title": title,
                "initial_persona": persona,
                "initial_model": model,
            },
            meta={},
        )
        return cls(
            session_id=sid,
            created_at=ts,
            initial_title=title,
            initial_persona=persona,
            initial_model=model,
            events=[meta_event],
        )

    @classmethod
    def from_events(cls, events: list[Event]) -> Session:
        """从已读到的事件流恢复一个 :class:`Session`。

        Args:
            events: 事件列表，**首条必须是** ``session_meta`` 类型。

        Returns:
            恢复出的 Session 实例。

        Raises:
            SessionCorruptError: 事件流为空、首条非 ``session_meta``、或缺
                必有 payload 字段。
        """
        if not events:
            raise SessionCorruptError("事件流为空，无法恢复 Session")
        head = events[0]
        if head.type != "session_meta":
            raise SessionCorruptError(f"首行必须是 session_meta 事件，实际: {head.type!r}")
        payload = head.payload
        for required in ("initial_title", "initial_persona", "initial_model"):
            if required not in payload:
                raise SessionCorruptError(f"session_meta 缺必有字段: {required}")
        return cls(
            session_id=head.uuid,
            created_at=head.ts,
            initial_title=payload["initial_title"],
            initial_persona=payload["initial_persona"],
            initial_model=payload["initial_model"],
            events=list(events),
        )

    @property
    def current_persona(self) -> str:
        """当前激活 persona：反向扫事件流找最后一条 ``persona_change``，没有则用 initial。"""
        for ev in reversed(self.events):
            if ev.type == "persona_change":
                to = ev.payload.get("to")
                if isinstance(to, str):
                    return to
        return self.initial_persona

    @property
    def current_model(self) -> str:
        """当前激活 model：同 :attr:`current_persona`，model 维度。"""
        for ev in reversed(self.events):
            if ev.type == "model_change":
                to = ev.payload.get("to")
                if isinstance(to, str):
                    return to
        return self.initial_model

    @property
    def messages(self) -> list[Message]:
        """过滤 ``user_message`` / ``assistant_message`` 事件，按 ts 顺序转 :class:`Message`。

        ``Event.uuid`` 直接映射到 ``Message.uuid``（M2.2 起的正式字段，design §4.6）。
        ``Event.meta`` 复制到 ``Message.meta``；``partial`` 标志合并进 ``Message.meta``。

        Returns:
            按时间顺序排列的 Message 列表。
        """
        result: list[Message] = []
        for ev in self.events:
            if ev.type == "user_message":
                result.append(
                    Message(
                        role="user",
                        content=ev.payload.get("content", ""),
                        timestamp=ev.ts,
                        meta=dict(ev.meta),
                        uuid=ev.uuid,
                    )
                )
            elif ev.type == "assistant_message":
                msg_meta: dict[str, Any] = dict(ev.meta)
                if ev.payload.get("partial"):
                    msg_meta["partial"] = True
                result.append(
                    Message(
                        role="assistant",
                        content=ev.payload.get("content", ""),
                        timestamp=ev.ts,
                        meta=msg_meta,
                        uuid=ev.uuid,
                    )
                )
        return result

    def append(self, event: Event) -> None:
        """仅追加事件到**内存**。

        IO 由 :class:`SessionStore` 负责，编排由 :class:`SessionManager` /
        :class:`Conversation` 负责——调用方应保证两边都更新（典型模式：先调
        ``store.append_event`` 成功后再调本方法）。
        """
        self.events.append(event)

    def to_dict(self) -> dict[str, Any]:
        """完整快照导出。

        Returns:
            形如 ``{"session_id": ..., "events": [...]}`` 的可 JSON 序列化 dict。
        """
        return {
            "session_id": self.session_id,
            "created_at": _serialize_ts(self.created_at),
            "initial_title": self.initial_title,
            "initial_persona": self.initial_persona,
            "initial_model": self.initial_model,
            "events": [
                {
                    "type": ev.type,
                    "uuid": ev.uuid,
                    "ts": _serialize_ts(ev.ts),
                    "payload": ev.payload,
                    "meta": ev.meta,
                }
                for ev in self.events
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        """从 :meth:`to_dict` 的输出恢复。

        Raises:
            SessionCorruptError: 数据结构损坏。
        """
        try:
            events = [
                Event(
                    type=ev["type"],
                    uuid=ev["uuid"],
                    ts=_parse_ts(ev["ts"]),
                    payload=ev["payload"],
                    meta=ev.get("meta", {}),
                )
                for ev in data["events"]
            ]
        except (KeyError, ValueError, TypeError) as e:
            raise SessionCorruptError(f"Session.from_dict 解析失败: {e}") from e
        return cls.from_events(events)
