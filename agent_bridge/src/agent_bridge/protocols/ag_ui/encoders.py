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
- 出错时 :class:`ag_ui.core.RunErrorEvent.message` / ``code`` 来自
  :func:`agent_bridge.errors.map_exception`——可恢复 vs 不可恢复的分类、
  拟人化文案、错误码标识全部由错误模型转换层集中决定（design §4.6）
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
from agent.runtime import AgentRuntime, UserEvent

from agent import (
    Conversation,
    ConversationEvent,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
)

from ...errors import map_exception

logger = logging.getLogger(__name__)


def encode_stream(
    conv_factory: Callable[[], Conversation],
    user_input: str,
    *,
    thread_id: str,
    run_id: str,
    accept: str | None = None,
    agent_runtime: AgentRuntime | None = None,
    run_conversation: Callable[[Conversation, str], Iterator[ConversationEvent]] | None = None,
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
        agent_runtime: 014 起新增。非 ``None`` 时每条 ``ConversationEvent``
            被同步镜像复制给 ``agent_runtime.listeners.fan_out_event``——push
            通道订阅者（按 ``kinds=user_turn`` 过滤）能看到这条 user 触发轮。
            ``None`` 时保持 006~013 行为，不镜像。
        run_conversation: 可选运行器；不传时调用 ``Conversation.stream``。编辑重发
            endpoint 通过这里切换到 ``Conversation.edit_resend_latest``，保持编码逻辑复用。
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
        mirror_user_event = (
            UserEvent(session_id=conv.session.session_id, user_input=user_input)
            if agent_runtime is not None
            else None
        )
        event_stream = (
            run_conversation(conv, user_input)
            if run_conversation is not None
            else conv.stream(user_input)
        )
        for ev in event_stream:
            # 014: 镜像复制给 push 通道订阅者（kinds=user_turn 时可见）
            if agent_runtime is not None and mirror_user_event is not None:
                try:
                    agent_runtime.listeners.fan_out_event(mirror_user_event, ev)
                except Exception:
                    # 镜像失败绝不影响 pull 路径——log warning 后继续
                    logger.warning("listener fan_out 失败", exc_info=True)
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
        err = map_exception(exc)
        yield _encode(
            RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=err.message,
                code=err.code,
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
