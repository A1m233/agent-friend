"""把内部 :class:`agent.ConversationEvent` 流编码为 OpenAI ChatCompletion 响应。

支持两种形态：

- :func:`encode_streaming`：``stream: true`` —— yield 一系列 SSE chunks，
  每个 chunk 是一个 ``chat.completion.chunk`` 对象，最后以 ``data: [DONE]`` 收尾
- :func:`encode_nonstreaming`：``stream: false`` —— 把整段流消费完拼成
  一个 ``chat.completion`` 对象

OpenAI 协议天然**不支持** ``ToolCallResult`` 事件——它假设客户端自执行 tool。
agent-bridge 是服务端自执行模型，所以工具调用中间过程对 OpenAI 客户端不可见，
客户端只能看到最终整合后的 assistant 文本（详见 design §4.3.4）。

每个 chunk 的 ``id`` / ``created`` / ``model`` 字段沿用同一份在路由层产生的
快照，供客户端聚合多个 chunk 时识别属于同一次响应。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from agent.runtime import AgentRuntime, UserEvent

from agent import Conversation, TextDelta, TurnDone

from ...errors import build_openai_error_envelope, map_exception

logger = logging.getLogger(__name__)


def _chunk_id() -> str:
    """生成兼容 OpenAI 形态的 chunk id（``chatcmpl-`` 前缀）。"""
    return f"chatcmpl-{uuid4().hex[:24]}"


def encode_streaming(
    conv: Conversation,
    user_input: str,
    *,
    model: str,
    agent_runtime: AgentRuntime | None = None,
) -> Iterator[bytes]:
    """流式编码：生成 SSE bytes 流。

    Args:
        conv: 已装配好的 Conversation。
        user_input: 本轮 user 输入。
        model: 透传给 chunk 的 model 字段。
        agent_runtime: 014 起新增；非 ``None`` 时每条 ConversationEvent 被同步
            镜像复制给 push listener（按 ``kinds=user_turn`` 过滤可见）。

    输出格式（每行末尾 ``\\n\\n``）::

        data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[
            {"index":0,"delta":{"role":"assistant"},"finish_reason":null}
        ]}

        data: {"id":"...","choices":[{"index":0,"delta":{"content":"hi"},...}]}
        ...
        data: {"id":"...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

        data: [DONE]

    Note:
        ``ToolCallRequest`` / ``ToolCallResult`` 事件在 OpenAI 协议下**不外露**
        ——它们的存在只会让 ``TextDelta`` 流出现"间隙"，客户端看起来就是 AI 思考
        了一会儿继续输出。这是 OpenAI 协议本身的局限（详见 design §4.3.4）。
    """
    response_id = _chunk_id()
    created = int(time.time())
    finish_reason = "stop"

    yield _sse_data(
        _build_chunk(
            response_id=response_id,
            created=created,
            model=model,
            delta={"role": "assistant", "content": ""},
            finish_reason=None,
        )
    )

    errored = False
    mirror_user_event = (
        UserEvent(session_id=conv.session.session_id, user_input=user_input)
        if agent_runtime is not None
        else None
    )
    try:
        for ev in conv.stream(user_input):
            # 014: 镜像复制给 push 通道订阅者
            if agent_runtime is not None and mirror_user_event is not None:
                try:
                    agent_runtime.listeners.fan_out_event(mirror_user_event, ev)
                except Exception:
                    logger.warning("listener fan_out 失败", exc_info=True)
            if isinstance(ev, TextDelta):
                yield _sse_data(
                    _build_chunk(
                        response_id=response_id,
                        created=created,
                        model=model,
                        delta={"content": ev.text},
                        finish_reason=None,
                    )
                )
            elif isinstance(ev, TurnDone) and ev.stop_reason == "max_turns_reached":
                finish_reason = "length"
            # ToolCallRequest / ToolCallResult: OpenAI 协议下不外露
    except Exception as exc:
        # 流式响应 HTTP 头已经发出（200 OK），无法改状态码；按 design §4.6.2 在
        # SSE 流中段补一条 error chunk，再 [DONE] 收尾——OpenAI SDK 能识别。
        errored = True
        logger.exception("OpenAI 流式编码失败")
        err = map_exception(exc)
        yield _sse_data(build_openai_error_envelope(err))
    finally:
        if not errored:
            yield _sse_data(
                _build_chunk(
                    response_id=response_id,
                    created=created,
                    model=model,
                    delta={},
                    finish_reason=finish_reason,
                )
            )
        yield b"data: [DONE]\n\n"


def encode_nonstreaming(
    conv: Conversation,
    user_input: str,
    *,
    model: str,
    agent_runtime: AgentRuntime | None = None,
) -> dict[str, Any]:
    """非流式编码：消费整段流拼成一个 ``chat.completion`` 对象。

    Args:
        conv: 已装配好的 Conversation。
        user_input: 本轮 user 输入。
        model: 透传给 response 的 model 字段。
        agent_runtime: 014 起新增；非 ``None`` 时同步镜像复制给 push listener。

    ``usage`` 字段本期填 0 占位（agent 核心库未跟踪 token 计数，详见 design §5.4）。
    """
    response_id = _chunk_id()
    created = int(time.time())
    text_parts: list[str] = []
    finish_reason = "stop"

    mirror_user_event = (
        UserEvent(session_id=conv.session.session_id, user_input=user_input)
        if agent_runtime is not None
        else None
    )
    for ev in conv.stream(user_input):
        if agent_runtime is not None and mirror_user_event is not None:
            try:
                agent_runtime.listeners.fan_out_event(mirror_user_event, ev)
            except Exception:
                logger.warning("listener fan_out 失败", exc_info=True)
        if isinstance(ev, TextDelta):
            text_parts.append(ev.text)
        elif isinstance(ev, TurnDone) and ev.stop_reason == "max_turns_reached":
            finish_reason = "length"

    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(text_parts),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _build_chunk(
    *,
    response_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None,
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _sse_data(chunk: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
