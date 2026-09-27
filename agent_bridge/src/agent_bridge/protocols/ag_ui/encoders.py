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
| runtime 主动取消     | 收尾未关闭 message + 带 cancelled result 的 ``RUN_FINISHED``    |
| 异常                | ``RUN_ERROR`` 后**不发** ``RUN_FINISHED``                        |

注意：

- 文本与工具调用穿插时，**前后两段 assistant 文本是两个独立 message**（不同
  ``message_id``）；AG-UI 协议规范如此，且 client SDK 据此分行展示
- ``ToolCallResult.is_error=True`` 时在 ``content`` 前缀 ``[error] ``——AG-UI
  ``ToolCallResultEvent`` 无专用 ``is_error`` 字段，约定字符串前缀承担信号
- 出错时 :class:`ag_ui.core.RunErrorEvent.message` / ``code`` 来自
  :func:`agent_bridge.errors.map_exception`——可恢复 vs 不可恢复的分类、
  用户可见文案、错误码标识全部由错误模型转换层集中决定（design §4.6）
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from typing import Any, cast
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
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
from agent.runtime import AgentRuntime, RunHandle, RunOutcome, UserEvent
from agent.system_prompt.sections import PromptChannel
from agent.system_prompt.surfaces import InteractionSurfaceId

from agent import (
    AssistantMessageEnd,
    AssistantMessagePhase,
    AssistantMessageReset,
    AssistantMessageStart,
    AssistantPhaseStatus,
    ContextCompactionStatus,
    Conversation,
    ConversationEvent,
    ModelResponseStatus,
    SkillActivationStatus,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
)

from ...errors import map_exception
from ...session_turns import SessionTurnCoordinator

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
    channel: PromptChannel | None = None,
    surface: InteractionSurfaceId | None = None,
    turn_coordinator: SessionTurnCoordinator | None = None,
    run_handle: RunHandle | None = None,
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
        turn_coordinator: 可选 per-session turn 协调器。同一 ``thread_id`` 的持久化
            写入轮必须串行，避免下一轮读到上一轮 finally 尚未落完的半旧状态。
    """
    encoder = EventEncoder(accept=accept or "")

    def _encode(event: Any) -> bytes:
        if run_handle is not None and isinstance(
            event, (RunStartedEvent, RunFinishedEvent, RunErrorEvent)
        ):
            snapshot = run_handle.snapshot()
            accepted = (
                run_handle.request.input_accepted
                if isinstance(event, RunStartedEvent)
                else snapshot.input_accepted
            )
            fields: dict[str, Any] = {
                "inputAccepted": accepted,
                "runtimeInstanceId": snapshot.runtime_instance_id,
            }
            if isinstance(event, RunErrorEvent):
                fields.update(threadId=snapshot.session_id, runId=snapshot.run_id)
            if isinstance(event, RunFinishedEvent):
                fields["result"] = {**(event.result or {}), "inputAccepted": accepted}
            event = event.model_copy(update=fields)
        return encoder.encode(event).encode("utf-8")

    open_message_id: str | None = None
    open_items: set[str] = set()
    conv: Conversation | None = None

    turn_scope = (
        turn_coordinator.hold(thread_id)
        if turn_coordinator is not None and run_handle is None
        else nullcontext()
    )
    with turn_scope:
        yield _encode(
            RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=thread_id,
                run_id=run_id,
            )
        )

        try:
            if run_handle is not None:
                event_stream = cast(Iterator[ConversationEvent], run_handle.events())
                mirror_user_event = None
            else:
                conv = conv_factory()
                mirror_user_event = (
                    UserEvent(session_id=conv.session.session_id, user_input=user_input)
                    if agent_runtime is not None
                    else None
                )
                event_stream = (
                    run_conversation(conv, user_input)
                    if run_conversation is not None
                    else conv.stream(user_input, channel=channel, surface=surface, run_id=run_id)
                )
            for ev in event_stream:
                # 014: 镜像复制给 push 通道订阅者（kinds=user_turn 时可见）
                if (
                    run_handle is None
                    and agent_runtime is not None
                    and mirror_user_event is not None
                ):
                    try:
                        agent_runtime.listeners.fan_out_event(mirror_user_event, ev)
                    except Exception:
                        # 镜像失败绝不影响 pull 路径——log warning 后继续
                        logger.warning("listener fan_out 失败", exc_info=True)
                if isinstance(ev, AssistantMessageStart):
                    open_items.add(ev.message_id)
                    yield _encode(
                        TextMessageStartEvent(
                            type=EventType.TEXT_MESSAGE_START,
                            message_id=ev.message_id,
                            role="assistant",
                        )
                    )
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.assistant_message",
                            value={
                                "messageId": ev.message_id,
                                "phase": ev.phase,
                                "itemIndex": ev.item_index,
                            },
                        )
                    )
                elif isinstance(ev, AssistantMessagePhase):
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.assistant_message_phase",
                            value={"messageId": ev.message_id, "phase": ev.phase},
                        )
                    )
                elif isinstance(ev, AssistantMessageReset):
                    open_items.difference_update(ev.message_ids)
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.assistant_message_reset",
                            value={"messageIds": ev.message_ids},
                        )
                    )
                elif isinstance(ev, AssistantMessageEnd):
                    if ev.message_id in open_items:
                        yield _encode(
                            TextMessageEndEvent(
                                type=EventType.TEXT_MESSAGE_END,
                                message_id=ev.message_id,
                            )
                        )
                        open_items.remove(ev.message_id)
                elif isinstance(ev, AssistantPhaseStatus):
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.assistant_phase",
                            value={
                                "runId": ev.run_id,
                                "mode": ev.mode,
                                "validation": ev.validation,
                            },
                        )
                    )
                elif isinstance(ev, TextDelta) and ev.message_id is not None:
                    if ev.message_id not in open_items:
                        open_items.add(ev.message_id)
                        yield _encode(
                            TextMessageStartEvent(
                                type=EventType.TEXT_MESSAGE_START,
                                message_id=ev.message_id,
                                role="assistant",
                            )
                        )
                        yield _encode(
                            CustomEvent(
                                type=EventType.CUSTOM,
                                name="agent_friend.assistant_message",
                                value={
                                    "messageId": ev.message_id,
                                    "phase": ev.phase,
                                    "itemIndex": ev.item_index,
                                },
                            )
                        )
                    yield _encode(
                        TextMessageContentEvent(
                            type=EventType.TEXT_MESSAGE_CONTENT,
                            message_id=ev.message_id,
                            delta=ev.text,
                        )
                    )
                elif isinstance(ev, TextDelta):
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
                    wire_tool_call_id = ev.event_tool_call_id or ev.tool_call_id
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
                            tool_call_id=wire_tool_call_id,
                            tool_call_name=ev.tool_name,
                        )
                    )
                    yield _encode(
                        ToolCallArgsEvent(
                            type=EventType.TOOL_CALL_ARGS,
                            tool_call_id=wire_tool_call_id,
                            delta=ev.arguments_json or _dump_args(ev.args),
                        )
                    )
                    yield _encode(
                        ToolCallEndEvent(
                            type=EventType.TOOL_CALL_END,
                            tool_call_id=wire_tool_call_id,
                        )
                    )

                elif isinstance(ev, ToolCallResult):
                    wire_tool_call_id = ev.event_tool_call_id or ev.tool_call_id
                    content = ev.text if not ev.is_error else f"[error] {ev.text}"
                    yield _encode(
                        ToolCallResultEvent(
                            type=EventType.TOOL_CALL_RESULT,
                            message_id=_new_message_id(),
                            tool_call_id=wire_tool_call_id,
                            content=content,
                            role="tool",
                        )
                    )

                elif isinstance(ev, ModelResponseStatus):
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.run_status",
                            value={
                                "phase": ev.phase,
                                "afterTool": ev.after_tool,
                                "attempt": ev.attempt,
                                "maxAttempts": ev.max_attempts,
                            },
                        )
                    )

                elif isinstance(ev, ContextCompactionStatus):
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.context_compaction",
                            value={
                                "activityId": ev.activity_id,
                                "phase": ev.phase,
                            },
                        )
                    )

                elif isinstance(ev, SkillActivationStatus):
                    yield _encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="agent_friend.skill_activation",
                            value={
                                "status": ev.status,
                                "skillId": ev.skill_id,
                                "name": ev.name,
                                "source": ev.source,
                                "invocationKind": ev.invocation_kind,
                                "reason": ev.reason,
                                "errorCode": ev.error_code,
                                "errorMessage": ev.error_message,
                                "order": ev.order,
                            },
                        )
                    )

                elif isinstance(ev, TurnDone):
                    # TurnDone 仅作为流结束信号；结构化 status/reason/usage 在
                    # Scheduler 完成 RunTerminal 后统一映射到 RUN_FINISHED。
                    pass

        except Exception as exc:
            for identity in sorted(open_items):
                yield _encode(
                    TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=identity)
                )
            open_items.clear()
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
            if conv is not None:
                try:
                    conv.append_run_error_event(
                        code=err.code,
                        message=err.message,
                        recoverable=err.recoverable,
                        source="ag_ui",
                        run_id=run_id,
                        sanitize_failed_model_call=err.sanitize_failed_model_call,
                        reason=err.reason,
                        recovery_action=err.recovery_action,
                    )
                except Exception:
                    logger.warning("AG-UI run_error 持久化失败", exc_info=True)
            yield _encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=err.message,
                    code=err.code,
                    raw_event=err.recovery_details(),
                )
            )
            return

        for identity in sorted(open_items):
            yield _encode(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=identity))
        open_items.clear()
        if run_handle is not None:
            terminal = run_handle.terminal
            if terminal is not None and terminal.outcome is RunOutcome.CANCELLED:
                if open_message_id is not None:
                    yield _encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END,
                            message_id=open_message_id,
                        )
                    )
                    open_message_id = None
                yield _encode(
                    RunFinishedEvent(
                        type=EventType.RUN_FINISHED,
                        thread_id=thread_id,
                        run_id=run_id,
                        result={
                            "outcome": "cancelled",
                            "reasonCode": terminal.reason_code,
                        },
                    )
                )
                return
            if terminal is None or terminal.outcome is not RunOutcome.COMPLETED:
                if open_message_id is not None:
                    yield _encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END,
                            message_id=open_message_id,
                        )
                    )
                    open_message_id = None
                message = "本轮对话未能完成，请稍后重试"
                code = terminal.reason_code if terminal is not None else "runtime_error"
                recovery_details = None
                if terminal is not None and terminal.error is not None:
                    err = map_exception(terminal.error)
                    message = err.message
                    code = err.code
                    recovery_details = err.recovery_details()
                yield _encode(
                    RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message=message,
                        code=code,
                        raw_event=recovery_details,
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

        terminal = run_handle.terminal if run_handle is not None else None
        yield _encode(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=thread_id,
                run_id=run_id,
                result=(
                    {
                        "outcome": "completed",
                        "status": (
                            str(terminal.turn_status)
                            if terminal is not None and terminal.turn_status is not None
                            else "completed"
                        ),
                        "reasonCode": terminal.reason_code if terminal is not None else "end_turn",
                        "toolCallItems": terminal.tool_call_items,
                        "executedToolCalls": terminal.total_tool_calls,
                        "modelCalls": terminal.model_calls,
                        "modelContinuations": terminal.model_continuations,
                        "budgetDimension": terminal.budget_dimension,
                    }
                    if terminal is not None
                    else None
                ),
            )
        )


def _new_message_id() -> str:
    return uuid4().hex


def _dump_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)
