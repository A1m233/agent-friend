"""``Message`` 数据结构：对话消息的统一表示。

所有模块（``agent`` / ``memory`` / ``tools.cli``）通过本类型交换消息。
``llm_providers`` 不感知本类型，使用 OpenAI 风格的 ``dict``，由 ``Conversation``
负责互转。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.2.2 与
docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.3。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

Role = Literal["system", "user", "assistant", "tool"]
"""消息角色。``"tool"`` 起自 005，承载工具执行结果回喂给 LLM 的消息。"""


@dataclass
class Message:
    """单条对话消息。

    Attributes:
        role: 消息角色，:data:`Role` 之一。
        content: 消息正文。``role="tool"`` 时是工具执行结果文本；
            ``role="assistant"`` 在决定调用工具但暂无文字回复时可能为空字符串。
        timestamp: 创建时间，默认当前时间。
        meta: 可扩展元数据字典。常见字段：

            - ``partial`` (bool) — 流式被中断的回复
            - ``persona`` / ``model`` — assistant 消息生成时的快照（002）
            - ``tool_call_id`` (str) — ``role="tool"`` 必带，关联到对应的
              assistant 消息中的 ``tool_calls[*].id``（005）
            - ``tool_name`` (str) — ``role="tool"`` 携带，便于调试观测（005）
            - ``tool_calls`` (list) — ``role="assistant"`` 决定调工具时携带；
              元素形如 ``{"id": str, "name": str, "args": dict}``，
              :meth:`to_openai` 会转成 OpenAI 协议格式（005）
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

    def to_openai(self) -> dict[str, Any]:
        """转为 OpenAI / LiteLLM 期望的消息格式。

        基础字段是 ``{"role": ..., "content": ...}``。在两种 role 下追加扩展字段：

        - ``role="tool"``：追加 ``tool_call_id`` 字段（取自 ``meta["tool_call_id"]``，
          缺失时为空字符串——OpenAI 会在调用时报错，便于发现上游 bug）。
        - ``role="assistant"`` 且 ``meta["tool_calls"]`` 非空：追加 ``tool_calls``
          字段，每项形如 ``{"id", "type": "function", "function": {"name", "arguments"}}``，
          其中 ``arguments`` 是入参的 **JSON 字符串**（OpenAI 协议要求，不是 dict）。

        Returns:
            可直接喂给 :meth:`llm_providers.LLMClient.stream` /
            :meth:`llm_providers.LLMClient.complete` 的字典。

        Note:
            返回类型从 005 起放宽为 ``dict[str, Any]``（原 ``dict[str, str]``）；
            扩展字段含 ``list`` / 嵌套 ``dict``。详见 005 design §1.3 / §6.1。
        """
        out: dict[str, Any] = {"role": self.role, "content": self.content}

        if self.role == "tool":
            out["tool_call_id"] = self.meta.get("tool_call_id", "")
        elif self.role == "assistant":
            tool_calls = self.meta.get("tool_calls")
            if tool_calls:
                out["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]

        return out

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
