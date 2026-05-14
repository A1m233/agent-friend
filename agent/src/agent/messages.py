"""``Message`` 数据结构：对话消息的统一表示。

所有模块（``agent`` / ``memory`` / ``tools.cli``）通过本类型交换消息。
``llm_providers`` 不感知本类型，使用 OpenAI 风格的 ``dict``，由 ``Conversation``
负责互转。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.2.2。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    """单条对话消息。

    Attributes:
        role: 消息角色，``"system"`` / ``"user"`` / ``"assistant"`` 之一。
        content: 消息正文。
        timestamp: 创建时间，默认当前时间。
        meta: 可扩展元数据字典。阶段 2 用于标记 memory 注入消息、token 数等；
            流式被中断的回复会带 ``meta["partial"] = True``。
        uuid: 消息级唯一标识（uuid4 字符串）。默认值由工厂自动生成，
            构造时**无需显式传入**——现有调用代码（CLI / 测试）保持兼容。
            对于从事件流派生的消息，:meth:`Session.messages` 会用对应
            ``Event.uuid`` 填充本字段（详见 002 design §4.6）。
    """

    role: Role
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    meta: dict[str, Any] = field(default_factory=dict)
    uuid: str = field(default_factory=lambda: str(uuid4()))

    def to_openai(self) -> dict[str, str]:
        """转为 OpenAI / LiteLLM 期望的 ``{"role": ..., "content": ...}`` 格式。"""
        return {"role": self.role, "content": self.content}

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化的 dict。"""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """从 :meth:`to_dict` 的输出反序列化。

        Note:
            缺 ``uuid`` 字段的旧数据会**自动补一个新 uuid**（field 默认值工厂触发），
            不强制要求迁移。
        """
        kwargs: dict[str, Any] = {
            "role": data["role"],
            "content": data["content"],
            "timestamp": datetime.fromisoformat(data["timestamp"]),
            "meta": data.get("meta", {}),
        }
        if "uuid" in data:
            kwargs["uuid"] = data["uuid"]
        return cls(**kwargs)
