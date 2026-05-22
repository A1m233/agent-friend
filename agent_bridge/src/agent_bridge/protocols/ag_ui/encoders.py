"""把内部 :class:`agent.ConversationEvent` 流编码为 AG-UI SSE 事件。

事件映射（详见 design §4.4.3）：

| ConversationEvent  | AG-UI 事件序列                                                  |
| ------------------ | --------------------------------------------------------------- |
| 开场               | ``RUN_STARTED``                                                 |
| 首个 ``TextDelta``  | ``TEXT_MESSAGE_START`` + ``TEXT_MESSAGE_CONTENT``               |
| 后续 ``TextDelta``  | ``TEXT_MESSAGE_CONTENT``                                        |
| ``ToolCallRequest``  | 切走当前文本 message → ``TEXT_MESSAGE_END`` 然后 ``TOOL_CALL_START`` + ``TOOL_CALL_ARGS``（整段 args JSON 一次性 delta）+ ``TOOL_CALL_END`` |
| ``ToolCallResult``   | ``TOOL_CALL_RESULT``                                            |
| ``TurnDone``         | 收尾未关闭 message（``TEXT_MESSAGE_END``）+ ``RUN_FINISHED``    |
| 异常                | ``RUN_ERROR`` 后**不发** ``RUN_FINISHED``                        |

注意：

- 文本与工具调用穿插时，**前后两段 assistant 文本是两个独立 message**（不同
  ``message_id``）；AG-UI 协议规范如此，且 client SDK 据此分行展示
- ``ToolCallResult.is_error=True`` 时在 ``content`` 前缀 ``[error] ``——AG-UI
  ``ToolCallResultEvent`` 无专用 ``is_error`` 字段，约定字符串前缀承担信号
- 出错时 :class:`ag_ui.core.RunErrorEvent.message` 用拟人化文案兜底；技术细节
  写入 ``code`` 字段（design §4.6 错误模型）。本期只在最外层 try/except 兜底，
  完整错误模型转换层留 M6.3
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

from ag_ui.core import (
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from agent import (
    Conversation,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
)

logger = logging.getLogger(__name__)


def encode_stream(
    conv_factory: Callable[[], Conversation],
    user_input: str,
    *,
    thread_id: str,
    run_id: str,
    accept: str | None = None,
) -> Iterator[bytes]:
    """流式编码 AG-UI SSE bytes。

    Args:
        conv_factory: 无参 callable，调用一次得到 :class:`Conversation`。装配过程
            （session 打开 / 创建、persona 查询、LLM client 构造）放在这里，
            从而把装配阶段失败和 stream 阶段失败**统一**为 ``RUN_ERROR``
            事件——客户端永远以 ``RUN_STARTED`` 开头看到这一轮，要么以
            ``RUN_FINISHED`` 收尾，要么以 ``RUN_ERROR`` 收尾，无歧义。
        user_input: 本轮 ``new_user_input``，转发给 ``Conversation.stream``。
        thread_id: ``RUN_STARTED`` / ``RUN_FINISHED`` 事件回填。
        run_id: 同上。
        accept: HTTP ``Accept`` header；交给 :class:`EventEncoder` 决定具体
            wire 格式（SSE / NDJSON 等）。
    """
    encoder = EventEncoder(accept=accept or "")

    def _encode(event: Any) -> bytes:
        return encoder.encode(event).encode("utf-8")

    open_message_id: str | None = None

    yield _encode(
        RunStartedEvent(
            type=EventType.RUN_STARTED,
            thread_id=thread_id,
            run_id=run_id,
        )
    )

    try:
        conv = conv_factory()
        for ev in conv.stream(user_input):
            if isinstance(ev, TextDelta):
                if open_message_id is None:
                    open_message_id = _new_message_id()
                    yield _encode(
                        TextMessageStartEvent(
                            type=EventType.TEXT_MESSAGE_START,
                            message_id=open_message_id,
                            role="assistant",
                        )
                    )
                yield _encode(
                    TextMessageContentEvent(
                        type=EventType.TEXT_MESSAGE_CONTENT,
                        message_id=open_message_id,
                        delta=ev.text,
                    )
                )

            elif isinstance(ev, ToolCallRequest):
                if open_message_id is not None:
                    yield _encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END,
                            message_id=open_message_id,
                        )
                    )
                    open_message_id = None

                yield _encode(
                    ToolCallStartEvent(
                        type=EventType.TOOL_CALL_START,
                        tool_call_id=ev.tool_call_id,
                        tool_call_name=ev.tool_name,
                    )
                )
                yield _encode(
                    ToolCallArgsEvent(
                        type=EventType.TOOL_CALL_ARGS,
                        tool_call_id=ev.tool_call_id,
                        delta=_dump_args(ev.args),
                    )
                )
                yield _encode(
                    ToolCallEndEvent(
                        type=EventType.TOOL_CALL_END,
                        tool_call_id=ev.tool_call_id,
                    )
                )

            elif isinstance(ev, ToolCallResult):
                content = ev.text if not ev.is_error else f"[error] {ev.text}"
                yield _encode(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=_new_message_id(),
                        tool_call_id=ev.tool_call_id,
                        content=content,
                        role="tool",
                    )
                )

            elif isinstance(ev, TurnDone):
                # TurnDone 仅作为"流结束"信号，AG-UI 用 RUN_FINISHED 承担。
                # max_turns_reached 等 stop_reason 信息本期不映射到 AG-UI 事件——
                # 客户端通过缺少 RUN_FINISHED 或后续 RunStartedEvent 推断异常。
                pass

    except Exception as exc:
        if open_message_id is not None:
            yield _encode(
                TextMessageEndEvent(
                    type=EventType.TEXT_MESSAGE_END,
                    message_id=open_message_id,
                )
            )
            open_message_id = None
        logger.exception("AG-UI run %s 失败", run_id)
        yield _encode(
            RunErrorEvent(
                type=EventType.RUN_ERROR,
                message="出了点小状况，刚刚没顺利想完那段，可以再说一次试试。",
                code=type(exc).__name__,
            )
        )
        return

    if open_message_id is not None:
        yield _encode(
            TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END,
                message_id=open_message_id,
            )
        )

    yield _encode(
        RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            thread_id=thread_id,
            run_id=run_id,
        )
    )


def _new_message_id() -> str:
    return uuid4().hex


def _dump_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)
