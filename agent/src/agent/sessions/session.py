"""``Session`` —— 会话聚合根。

承载会话元数据 + 事件流；**不负责持久化**（IO 由 :class:`SessionStore` 完成），
**不负责对话执行**（由 :class:`Conversation` 完成）。

详见 docs/requirements/002-engine-session-management/design.md §4.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from ..messages import Message
from .errors import SessionCorruptError
from .events import SCHEMA_VERSION, Event, _ensure_utc, _parse_ts, _serialize_ts

Channel = Literal["voice", "text"]
"""会话与用户的交流模态。

007 起引入：``"voice"`` 表示语音通话场景，``"text"`` 表示传统文字对话。
session 默认为 ``"text"``——所有 007 之前创建的老 session 在
``current_channel`` 上都会 fallback 到 ``"text"``，行为完全向后兼容。
"""


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
        从 :attr:`active_events` **派生**（单一真相源 + active projection）。
        事件流是 append-only，所以这些派生属性永远与文件状态一致；编辑重发仅
        通过 ``turn_rewrite`` marker 让旧分支在投影视图里失活。
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
        persona_id: str | None = None,
        session_id: str | None = None,
        created_at: datetime | None = None,
        channel: Channel = "text",
    ) -> Session:
        """创建一个**全新**的 :class:`Session`，并自动生成首行 ``session_meta`` 事件。

        Args:
            title: 初始标题。
            persona: 初始 persona 名（slug；显示用）。
            model: 初始 model 名（LiteLLM 风格，如 ``"deepseek/deepseek-chat"``）。
            persona_id: 初始 persona 的 UUID（003 起的主键）；为兼容入参传 ``None``
                时不写入 ``initial_persona_id`` 字段——读老文件场景配套。**新建场景
                推荐显式传**。
            session_id: 可选；不传则自动 uuid4。
            created_at: 可选；不传则用 :func:`datetime.now` UTC 当前时间。
            channel: 初始 channel（007 起新增）。``"text"`` 时**不**写入
                ``session_meta.payload.initial_channel`` 字段（与 002~006 老文件
                完全字节兼容）；``"voice"`` 时写入，让 :attr:`current_channel`
                能从 session_meta 派生。

        Returns:
            包含一条 ``session_meta`` 事件的新 Session 实例。

        Note:
            返回后**还没落盘**——调用方应紧接着 ``store.create(session)`` 写文件。
        """
        sid = session_id or _new_session_id()
        ts = _ensure_utc(created_at) if created_at else datetime.now(UTC)
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "initial_title": title,
            "initial_persona": persona,
            "initial_model": model,
        }
        if persona_id is not None:
            payload["initial_persona_id"] = persona_id
        if channel != "text":
            payload["initial_channel"] = channel
        meta_event = Event(
            type="session_meta",
            uuid=sid,
            ts=ts,
            payload=payload,
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
    def active_events(self) -> list[Event]:
        """当前有效事件流。

        ``turn_rewrite`` 本身是 append-only marker，不进入业务投影；其
        ``payload.inactive_event_uuids`` 指向的旧分支事件也从投影视图中剔除。
        原始 :attr:`events` 保持完整，供审计 / 回放 / 调试使用。
        """
        inactive: set[str] = set()
        for ev in self.events:
            if ev.type != "turn_rewrite":
                continue
            raw = ev.payload.get("inactive_event_uuids", [])
            if isinstance(raw, list):
                inactive.update(item for item in raw if isinstance(item, str))

        if not inactive:
            return [ev for ev in self.events if ev.type != "turn_rewrite"]
        return [ev for ev in self.events if ev.type != "turn_rewrite" and ev.uuid not in inactive]

    @property
    def current_persona(self) -> str:
        """当前激活 persona 的 **name**（向后兼容名；003 起等同 :attr:`current_persona_name`）。

        反向扫事件流找最后一条 ``persona_change``，没有则用 ``initial_persona``。
        """
        return self.current_persona_name

    @property
    def current_persona_name(self) -> str:
        """当前激活 persona 的 name（slug）。

        反向扫 ``persona_change`` 事件：优先 ``payload.to``（新 schema 与旧 schema 都
        把 name 存在 ``to``），没有 persona_change 时用 ``initial_persona``。
        """
        for ev in reversed(self.active_events):
            if ev.type == "persona_change":
                to = ev.payload.get("to")
                if isinstance(to, str):
                    return to
        return self.initial_persona

    @property
    def current_persona_id(self) -> str | None:
        """当前激活 persona 的 UUID（003 起的主键）。

        反向扫 ``persona_change`` 事件：优先 ``payload.to_id``（003 新 schema）；
        若事件只含老字段 ``to`` 则**本属性放弃尝试在此处查 catalog**（保持 sessions
        模块独立于 personas），返回 ``None``。没有 persona_change 时 fallback 到
        ``session_meta.payload.initial_persona_id``（可能也是 ``None``，老文件）。

        调用方拿到 ``None`` 时应自行用 :class:`PersonaCatalog.find_by_name(current_persona_name)`
        反查。
        """
        for ev in reversed(self.active_events):
            if ev.type == "persona_change":
                to_id = ev.payload.get("to_id")
                if isinstance(to_id, str):
                    return to_id
                return None  # 老事件无 to_id，不再继续向前扫（语义已断）
        head = self.events[0] if self.events else None
        if head is not None and head.type == "session_meta":
            initial_id = head.payload.get("initial_persona_id")
            if isinstance(initial_id, str):
                return initial_id
        return None

    @property
    def current_model(self) -> str:
        """当前激活 model：同 :attr:`current_persona`，model 维度。"""
        for ev in reversed(self.active_events):
            if ev.type == "model_change":
                to = ev.payload.get("to")
                if isinstance(to, str):
                    return to
        return self.initial_model

    @property
    def current_channel(self) -> Channel:
        """当前激活 channel（007 起新增）。

        反向扫 ``channel_change`` 事件：优先 ``payload.to``。没有 channel_change
        时 fallback 到 ``session_meta.payload.initial_channel``；老文件无该字段时
        默认 ``"text"``。
        """
        for ev in reversed(self.active_events):
            if ev.type == "channel_change":
                to = ev.payload.get("to")
                if isinstance(to, str) and to in ("voice", "text"):
                    return to  # type: ignore[return-value]
        head = self.events[0] if self.events else None
        if head is not None and head.type == "session_meta":
            initial = head.payload.get("initial_channel")
            if isinstance(initial, str) and initial in ("voice", "text"):
                return initial  # type: ignore[return-value]
        return "text"

    @property
    def messages(self) -> list[Message]:
        """从事件流派生 :class:`Message` 列表，按时间顺序排列。

        识别的事件类型：

        - ``user_message`` → 一条 ``role="user"`` 消息
        - ``assistant_message`` → 一条 ``role="assistant"`` 消息
        - ``tool_call_request`` → **不直接产消息**，依附到上一条 assistant
          消息的 ``meta["tool_calls"]`` 列表里（OpenAI 协议要求 assistant
          消息携带 ``tool_calls`` 字段）
        - ``tool_call_result`` → 一条 ``role="tool"`` 消息

        ``Event.uuid`` 直接映射到 ``Message.uuid``（M2.2 起的正式字段，详见
        002 design §4.6）。``Event.meta`` 复制到 ``Message.meta``；
        ``partial`` 标志合并进 ``Message.meta``。

        损坏数据的容忍：``tool_call_request`` 找不到上一条 assistant 时
        **静默忽略**——避免历史读取爆炸；新写入不会出现这种情况。

        Note:
            009 起的 ``compaction`` 事件**不参与本投影**（不产 Message）——它只是
            append-only 流上的摘要折叠点 marker，折叠投影发生在 context manager
            内部（用 :meth:`latest_compaction` 派生的 summary）。本属性始终返回
            **原始全量**，memory 抽取不受折叠影响（009 design §6.4）。

        Returns:
            按时间顺序排列的 Message 列表，可直接喂给 :class:`ContextManager`。
        """
        result: list[Message] = []
        last_assistant: Message | None = None
        for ev in self.active_events:
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
                last_assistant = None
            elif ev.type == "assistant_message":
                msg_meta: dict[str, Any] = dict(ev.meta)
                if ev.payload.get("partial"):
                    msg_meta["partial"] = True
                msg = Message(
                    role="assistant",
                    content=ev.payload.get("content", ""),
                    timestamp=ev.ts,
                    meta=msg_meta,
                    uuid=ev.uuid,
                )
                result.append(msg)
                last_assistant = msg
            elif ev.type == "tool_call_request":
                if last_assistant is None:
                    continue  # 数据损坏，静默忽略
                tool_calls = list(last_assistant.meta.get("tool_calls", []))
                tool_calls.append(
                    {
                        "id": ev.payload.get("tool_call_id", ""),
                        "name": ev.payload.get("tool_name", ""),
                        "args": ev.payload.get("args", {}),
                    }
                )
                last_assistant.meta["tool_calls"] = tool_calls
            elif ev.type == "tool_call_result":
                tool_meta: dict[str, Any] = dict(ev.meta)
                tool_meta["tool_call_id"] = ev.payload.get("tool_call_id", "")
                tool_meta["tool_name"] = ev.payload.get("tool_name", "")
                tool_meta["is_error"] = ev.payload.get("is_error", False)
                result.append(
                    Message(
                        role="tool",
                        content=ev.payload.get("content", ""),
                        timestamp=ev.ts,
                        meta=tool_meta,
                        uuid=ev.uuid,
                    )
                )
        return result

    def latest_compaction(self) -> Event | None:
        """最近一次上下文摘要折叠点（009 M3）；无则 ``None``。

        反向扫事件流找最后一条 ``compaction`` 事件。多次压缩天然叠加——最新
        compaction 的 summary 已蒸馏了此前全部内容，折叠永远只认最近一条。
        老文件（无 compaction 事件）返回 ``None``，自然退化为全量上下文。

        由 :class:`Conversation` 派生 :class:`agent.context.PriorSummary` 放进
        ``RuntimeContext`` 传给 context manager；折叠逻辑在 context manager 内部，
        不污染 :attr:`messages`（保持 memory 读全量、行为不变，009 design §6.4）。

        Returns:
            最近一条 ``compaction`` 事件，或 ``None``。
        """
        for ev in reversed(self.active_events):
            if ev.type == "compaction":
                return ev
        return None

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
