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
    user_indices = [index for index, message in enumerate(history) if message.role == "user"]
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
    return [index for index, message in enumerate(history) if message.role == "user"] + [
        len(history)
    ]
