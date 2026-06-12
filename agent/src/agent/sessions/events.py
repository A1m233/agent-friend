"""会话事件 schema。

每个 :class:`Event` 对应 JSONL 文件中的一行。**事件是 append-only、不可变的**——
内存里 :class:`Event` 是 ``frozen`` dataclass；文件中每行写入后不再修改。

详见 docs/requirements/002-engine-session-management/design.md §4.1。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from .errors import SessionCorruptError

EventType = Literal[
    "session_meta",
    "user_message",
    "assistant_message",
    "persona_change",
    "model_change",
    "channel_change",
    "tool_call_request",
    "tool_call_result",
    "compaction",
    "system_trigger",
    "memory_observation",
]

ALLOWED_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session_meta",
        "user_message",
        "assistant_message",
        "persona_change",
        "model_change",
        "channel_change",
        "tool_call_request",
        "tool_call_result",
        "compaction",
        "system_trigger",
        "memory_observation",
    }
)

SCHEMA_VERSION: Final[int] = 1
"""``session_meta.payload.schema_version`` 当前值。

未来真正修改 schema 时递增；本期不做迁移逻辑（002 design §1.2）。

005 起新增 ``tool_call_request`` / ``tool_call_result`` 两类事件——这是**纯加性
变更**，老文件不会触发新校验路径，故 ``SCHEMA_VERSION`` **不递增**
（详见 005 design §4.2.4）。

007 起新增 ``channel_change`` 事件 + ``session_meta.payload.initial_channel`` 字段，
继续遵循"纯加性变更不递增 SCHEMA_VERSION"约定（详见 007 design §4.9.1）。

009 起新增 ``compaction`` 事件（上下文摘要折叠点 marker），同样是纯加性——原始
消息事件一条不删不改，老文件无该事件时自然退化为全量。故 ``SCHEMA_VERSION``
**不递增**（详见 009 design §6.4）。

014 起新增 ``system_trigger`` / ``memory_observation`` 两类事件（main loop 系统
触发轮 marker / silent turn 产物），同样是纯加性——老文件不出现新 type；
``Session.messages`` 派生不识别新 type 自然忽略；故 ``SCHEMA_VERSION`` **不递增**
（详见 014 design §6.1）。
"""


def _ensure_utc(dt: datetime) -> datetime:
    """把 datetime 规整到 UTC：naive 视作 UTC，aware 转 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _serialize_ts(dt: datetime) -> str:
    """UTC ISO 8601 字符串，带 ``Z`` 后缀（如 ``2026-05-14T03:00:00Z``）。"""
    return _ensure_utc(dt).isoformat().replace("+00:00", "Z")


def _parse_ts(s: str) -> datetime:
    """解析 ISO 8601 字符串，兼容 ``Z`` 后缀和 ``+00:00`` 形式。"""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Event:
    """会话中的一条事件（user 消息 / assistant 回复 / persona 切换 等）。

    Attributes:
        type: 事件类型，详见 :data:`EventType`。
        uuid: 事件唯一标识，对 ``user_message`` / ``assistant_message`` 同时充当
            消息 uuid。建议用 uuid4。
        ts: 事件时间戳。**统一以 UTC 序列化**，naive datetime 视作 UTC。
        payload: type-specific 数据。各 type 的字段约定见 design §4.1.3。
        meta: 可扩展元数据。如 ``assistant_message.meta`` 常存生成它时的
            ``persona`` / ``model``。
    """

    type: EventType
    uuid: str
    ts: datetime
    payload: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        """序列化为一行 JSON 字符串（**不含**结尾 ``\\n``，由调用方追加）。

        ``ts`` 序列化为 UTC ISO 8601 字符串。
        """
        obj = {
            "type": self.type,
            "uuid": self.uuid,
            "ts": _serialize_ts(self.ts),
            "payload": self.payload,
            "meta": self.meta,
        }
        return json.dumps(obj, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> Event:
        """反序列化一行 JSON。

        Args:
            line: 单行 JSON 字符串（行尾 ``\\n`` 可有可无）。

        Returns:
            构造好的 :class:`Event` 实例。

        Raises:
            SessionCorruptError: 行不是合法 JSON、缺必有字段、或 ``type`` 不在
                :data:`ALLOWED_EVENT_TYPES` 中。
        """
        stripped = line.strip()
        if not stripped:
            raise SessionCorruptError("事件行为空")
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise SessionCorruptError(f"事件行非合法 JSON: {e}") from e
        if not isinstance(obj, dict):
            raise SessionCorruptError(f"事件行顶层必须是 JSON 对象，实际: {type(obj).__name__}")

        for required in ("type", "uuid", "ts", "payload"):
            if required not in obj:
                raise SessionCorruptError(f"事件缺必有字段: {required}")
        if obj["type"] not in ALLOWED_EVENT_TYPES:
            raise SessionCorruptError(f"未知事件类型: {obj['type']!r}")
        if not isinstance(obj["payload"], dict):
            raise SessionCorruptError(
                f"payload 必须是 JSON 对象，实际: {type(obj['payload']).__name__}"
            )
        meta = obj.get("meta", {})
        if not isinstance(meta, dict):
            raise SessionCorruptError(f"meta 必须是 JSON 对象，实际: {type(meta).__name__}")

        try:
            ts = _parse_ts(obj["ts"])
        except (ValueError, TypeError) as e:
            raise SessionCorruptError(f"ts 解析失败: {obj['ts']!r}: {e}") from e

        return cls(
            type=obj["type"],
            uuid=obj["uuid"],
            ts=ts,
            payload=obj["payload"],
            meta=meta,
        )
