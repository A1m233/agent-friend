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
    "assistant_phase",
    "image_delivery",
    "persona_change",
    "model_change",
    "model_config_change",
    "channel_change",
    "tool_call_request",
    "tool_call_result",
    "tool_execution_checkpoint",
    "model_output_rejection",
    "compaction",
    "tool_context_edit",
    "system_trigger",
    "memory_observation",
    "turn_rewrite",
    "run_error",
    "run_terminal",
    "run_started",
    "todo_reminder",
    "model_context",
    "provider_reasoning_state",
    "skill_catalog_state",
    "skill_context",
    "rollout_segment",
    "model_transcript_item",
    "rollout_request_prepared",
    "rollout_commit",
    "rollout_value",
]

ALLOWED_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session_meta",
        "user_message",
        "assistant_message",
        "assistant_phase",
        "image_delivery",
        "persona_change",
        "model_change",
        "model_config_change",
        "channel_change",
        "tool_call_request",
        "tool_call_result",
        "tool_execution_checkpoint",
        "model_output_rejection",
        "compaction",
        "tool_context_edit",
        "system_trigger",
        "memory_observation",
        "turn_rewrite",
        "run_error",
        "run_terminal",
        "run_started",
        "todo_reminder",
        "model_context",
        "provider_reasoning_state",
        "skill_catalog_state",
        "skill_context",
        "rollout_segment",
        "model_transcript_item",
        "rollout_request_prepared",
        "rollout_commit",
        "rollout_value",
    }
)

ROLLOUT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "rollout_segment",
        "model_transcript_item",
        "rollout_request_prepared",
        "rollout_commit",
        "rollout_value",
    }
)
"""Internal persistence events excluded from product/UI active projections."""

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

040 起新增 ``tool_context_edit`` 事件，记录模型视图中已被短占位替换的 tool
result uuid；原始 ``tool_call_result`` 仍完整保留，同样是纯加性变更，故
``SCHEMA_VERSION`` **不递增**。

014 起新增 ``system_trigger`` / ``memory_observation`` 两类事件（main loop 系统
触发轮 marker / silent turn 产物），同样是纯加性——老文件不出现新 type；通常不
参与 ``Session.messages``。后续仅对修复前的 Todo Reminder marker 合成只读历史状态，
不改变原始事件 schema；故 ``SCHEMA_VERSION`` **不递增**（详见 014 design §6.1）。

035 起新增 ``turn_rewrite`` 事件，用 append-only marker 标记一次"编辑并重发"
产生的失活事件集合；原始事件不删除不改，active projection 在 ``Session`` 内派生，
仍属于纯加性变更，故 ``SCHEMA_VERSION`` **不递增**。

    036 起新增 ``run_error`` 事件，用 append-only marker 记录一轮 assistant 运行失败
    的用户可见错误状态；它不参与 LLM history，仅供 UI 在刷新后恢复同一失败状态。
    纯加性变更，故 ``SCHEMA_VERSION`` **不递增**。

    068 的早期实现曾新增 ``model_context`` 事件。该类型继续允许读取，以兼容并
    审计已经落盘的旧 session，但不再投影到任何消息历史；当前动态运行时上下文
    只在每次模型调用前临时组装。事件 schema 没有破坏性变化，故
    ``SCHEMA_VERSION`` **不递增**。

    076 起新增 ``skill_catalog_state`` 与 ``skill_context`` 两类模型专用事件。
    它们不进入普通会话/UI/记忆投影，只进入 LLM history，仍是纯加性变更，故
    ``SCHEMA_VERSION`` **不递增**。

    095 起新增 ``model_output_rejection``，记录无法组成可靠 tool request/result
    对的模型输出失败。它只进入模型历史投影，不生成普通消息或工具卡片；同样是
    纯加性事件，故 ``SCHEMA_VERSION`` **不递增**。

    111 起新增 ``tool_execution_checkpoint`` 与 ``run_terminal``，分别记录副作用
    调用前的 write-ahead owner/recovery 事实和 run 唯一持久终态。二者不进入消息
    历史，不改写旧 request/result，仍为纯加性事件，故 ``SCHEMA_VERSION`` 不递增。

    113 起新增四类 ``rollout_*`` / ``model_transcript_item`` 事件，在同一 session
    JSONL 内持久化进程内 model transcript。它们不进入业务消息投影，reader 只应用
    完整 ``rollout_commit``，仍属于纯加性事件，故业务 ``SCHEMA_VERSION`` 不递增。
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
