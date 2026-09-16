"""Read-only recovery eligibility shared by admission, execution and clients."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..errors import AgentError
from .events import Event


@dataclass(frozen=True)
class ContinuationTarget:
    source_run_id: str
    root_run_id: str
    user_event: Event


def continuation_target(
    events: Sequence[Event],
    source_run_id: str | None = None,
    *,
    current_run_id: str | None = None,
) -> ContinuationTarget:
    """Select only the current task's latest stopped attempt, never a stale error."""
    boundary = next(
        (
            i
            for i in range(len(events) - 1, -1, -1)
            if events[i].type in {"user_message", "system_trigger"}
        ),
        None,
    )
    if boundary is None or events[boundary].type != "user_message":
        raise AgentError("当前会话没有可继续的用户任务")
    user = events[boundary]
    tail = list(events[boundary:])
    latest = next(
        (
            event
            for event in reversed(tail)
            if event.type in {"run_started", "run_terminal", "run_error"}
            and _run_id(event) != current_run_id
        ),
        None,
    )
    if latest is None or latest.type == "run_started":
        raise AgentError("任务尚未停止，不能重复启动")
    actual = _run_id(latest)
    if not actual or (source_run_id is not None and source_run_id != actual):
        raise AgentError("恢复目标已变化，请刷新后继续最新任务")
    terminal = next(
        (e for e in reversed(tail) if e.type == "run_terminal" and _run_id(e) == actual),
        None,
    )
    error = next(
        (e for e in reversed(tail) if e.type == "run_error" and _run_id(e) == actual),
        None,
    )
    if terminal is not None and terminal.payload.get("status") == "completed":
        raise AgentError("任务已经完成，无需继续")
    interrupted = terminal is not None and terminal.payload.get("status") == "interrupted"
    if not interrupted and (error is None or error.payload.get("recoverable") is not True):
        raise AgentError("这次失败不能直接继续，请先处理错误原因")

    # An unclosed transaction is not proof that nothing happened. Do not run a
    # model against an invented successful/failed result or relaunch the command.
    pending: set[str] = set()
    for event in tail:
        call_id = str(
            event.payload.get("event_tool_call_id") or event.payload.get("tool_call_id") or ""
        )
        if event.type == "tool_call_request":
            pending.add(call_id)
        elif event.type == "tool_call_result":
            pending.discard(call_id)
    if pending:
        raise AgentError("仍有工具执行结果待确认，请先等待或核实执行状态")
    started = next(
        (e for e in tail if e.type == "run_started" and _run_id(e) == actual),
        None,
    )
    root = str(started.payload.get("root_run_id") or actual) if started else actual
    return ContinuationTarget(actual, root, user)


def _run_id(event: Event) -> str:
    return str(
        event.meta.get("run_id")
        or event.payload.get("run_id")
        or (f"legacy:{event.uuid}" if event.type == "run_error" else "")
    )
