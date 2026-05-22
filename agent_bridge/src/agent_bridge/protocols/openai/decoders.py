"""把 OpenAI ChatCompletion request body 解码为 bridge 内部输入。

行为约定（详见 design §4.3.3）：

- ``role="system"`` 消息**丢弃**——agent 自家 ``SystemPromptComposer`` 装配
  最终 system prompt，不允许客户端覆盖（否则 persona / tools / runtime context
  会被吞）。客户端发的 system 消息记入日志方便调试
- ``role="tool"`` 消息**丢弃**——OpenAI 协议假设客户端代为执行 tool；
  agent-bridge 是服务端自执行 + 自整合，客户端不参与工具循环
- ``role="user"`` / ``role="assistant"`` 消息**保留**作为历史；最后一条
  ``role="user"`` 被剥离出来作为本轮 ``latest_user_input``

兼容性约定：

- ``content`` 既支持纯字符串（标准）也支持 OpenAI Vision 风格的 list（仅取
  其中 ``type="text"`` 的部分拼接）——孵化期 vision 不接，但避免对方传过来时
  报 500
- 缺 ``messages`` / 整段为空 → 抛 :class:`DecodeError`，由路由层转 400
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent import Message

logger = logging.getLogger(__name__)


class DecodeError(ValueError):
    """OpenAI request body 不符合 ChatCompletion 协议最小约束。"""


@dataclass(frozen=True)
class DecodedRequest:
    """OpenAI request body 解码结果。

    Attributes:
        history: 已剥离最新 user 输入后的历史消息。已过滤 system / tool。
        latest_user_input: 最后一条 user message 的文本。
        model: 客户端指定的 model 名；若缺则为 ``None``，由路由层用 runtime
            的默认值兜底。
        stream: 是否流式（``stream: true``）。
    """

    history: list[Message]
    latest_user_input: str
    model: str | None
    stream: bool


def decode_chat_completion_request(body: dict[str, Any]) -> DecodedRequest:
    """解码一份 OpenAI ChatCompletion 请求 body。

    Raises:
        DecodeError: ``messages`` 缺失 / 非数组 / 全为空 / 最后一条非 user。
    """
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise DecodeError("messages 字段缺失或为空")

    kept: list[Message] = []
    dropped_system = 0
    dropped_tool = 0

    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        role = raw.get("role")
        content = _extract_content_text(raw.get("content"))
        if role == "system":
            dropped_system += 1
            continue
        if role == "tool":
            dropped_tool += 1
            continue
        if role not in ("user", "assistant"):
            continue
        kept.append(Message(role=role, content=content))

    if dropped_system or dropped_tool:
        logger.debug(
            "OpenAI 解码丢弃 role 消息：system=%d, tool=%d "
            "（agent-bridge 不允许客户端覆盖 system，且不参与 tool 循环）",
            dropped_system,
            dropped_tool,
        )

    if not kept:
        raise DecodeError("messages 内未找到任何 user / assistant 消息")
    if kept[-1].role != "user":
        raise DecodeError("messages 最后一条必须是 role=user")

    latest = kept.pop()
    return DecodedRequest(
        history=kept,
        latest_user_input=latest.content,
        model=_extract_model(body.get("model")),
        stream=bool(body.get("stream", False)),
    )


def _extract_content_text(content: Any) -> str:
    """把 OpenAI 消息 ``content`` 字段（str 或 list-of-parts）抽成纯文本。

    OpenAI Vision 等扩展协议会用 ``content=[{type:"text",text:...}, {type:"image_url",...}]``
    列表形态。本期只取其中 ``type="text"`` 的部分拼接；其他类型静默丢弃。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _extract_model(raw_model: Any) -> str | None:
    """提取 ``model`` 字段；非字符串或空串 → ``None``。"""
    if isinstance(raw_model, str) and raw_model.strip():
        return raw_model.strip()
    return None
