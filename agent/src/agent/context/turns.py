"""按完整 user turn 选择上下文，避免拆散工具调用协议组。"""

from __future__ import annotations

from dataclasses import dataclass

from ..messages import Message
from .budget import estimate_tokens


@dataclass(frozen=True)
class ContextTurn:
    """从一条 user 消息开始、到下一条 user 消息之前结束的完整轮。"""

    start: int
    end: int
    messages: tuple[Message, ...]
    estimated_tokens: int


def split_complete_turns(history: list[Message]) -> tuple[list[Message], list[ContextTurn]]:
    """返回首个 user 之前的异常前缀与按 user 边界切出的完整轮。"""
    user_indices = [index for index, message in enumerate(history) if is_real_user_message(message)]
    if not user_indices:
        return list(history), []

    prefix = list(history[: user_indices[0]])
    turns: list[ContextTurn] = []
    for position, start in enumerate(user_indices):
        end = user_indices[position + 1] if position + 1 < len(user_indices) else len(history)
        messages = tuple(history[start:end])
        turns.append(
            ContextTurn(
                start=start,
                end=end,
                messages=messages,
                estimated_tokens=estimate_tokens(list(messages)),
            )
        )
    return prefix, turns


def recent_turn_start_candidates(history: list[Message]) -> list[int]:
    """返回从旧到新的合法连续后缀起点，最后包含空历史起点。"""
    return [index for index, message in enumerate(history) if is_real_user_message(message)] + [
        len(history)
    ]


def is_real_user_message(message: Message) -> bool:
    """Host advisories, Skill instructions and task anchors never open a user turn."""
    return (
        message.role == "user"
        and not message.meta.get("synthetic")
        and not message.meta.get("model_only")
        and not message.meta.get("trusted_host_context")
    )


def safe_compaction_boundaries(history: list[Message]) -> list[int]:
    """Return prefix ends which preserve complete responses and tool transactions.

    A provider response can occupy several assistant items followed by parallel
    results. A boundary cannot bisect any of those identities. Open or malformed
    transactions fail closed rather than inventing a result for compaction.
    """
    pending: dict[str, int] = {}
    pending_group: object = None
    response_ranges: dict[str, tuple[int, int]] = {}
    intervals: list[tuple[int, int]] = []
    for index, message in enumerate(history):
        if message.role == "tool":
            call_id = message.meta.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ValueError("orphan_tool_result")
            intervals.append((pending.pop(call_id), index))
            continue
        group = message.meta.get("assistant_response_id")
        if pending and not (message.role == "assistant" and group and group == pending_group):
            raise ValueError("tool_transaction_pending")
        pending_group = group
        if message.role != "assistant":
            continue
        if isinstance(group, str) and group:
            start = response_ranges.get(group, (index, index))[0]
            response_ranges[group] = (start, index)
        calls = message.meta.get("tool_calls", [])
        if not isinstance(calls, list):
            raise ValueError("invalid_tool_calls")
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else None
            if not isinstance(call_id, str) or not call_id or call_id in pending:
                raise ValueError("invalid_tool_call_identity")
            pending[call_id] = index
    if pending:
        raise ValueError("tool_transaction_pending")
    intervals.extend(response_ranges.values())
    blocked = {cut for start, end in intervals for cut in range(start + 1, end + 1)}
    return [cut for cut in range(1, len(history) + 1) if cut not in blocked]


def preserve_latest_skill_catalog(
    original: list[Message], retained: list[Message]
) -> list[Message]:
    """Keep the latest catalog world state when a context strategy drops older history."""
    latest = next(
        (
            message
            for message in reversed(original)
            if message.meta.get("synthetic") == "skill_catalog_state"
        ),
        None,
    )
    if latest is None or any(message.uuid == latest.uuid for message in retained):
        return list(retained)
    return [latest, *retained]
