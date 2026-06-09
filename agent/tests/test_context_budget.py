"""009 M1：``budget`` 的 token 估算与阈值推导单测。"""

from __future__ import annotations

from agent.context import BudgetSnapshot, make_budget_snapshot
from agent.context.budget import (
    BUFFER_RATIO,
    CHARS_TO_TOKENS,
    OUTPUT_RESERVE_RATIO,
    estimate_tokens,
)
from agent.messages import Message


def test_estimate_tokens_char_based() -> None:
    """纯字符 × 系数；偏保守上界。"""
    msgs = [Message(role="user", content="a" * 100)]
    assert estimate_tokens(msgs) == int(100 * CHARS_TO_TOKENS)


def test_estimate_tokens_sums_all_messages() -> None:
    msgs = [
        Message(role="system", content="x" * 40),
        Message(role="user", content="y" * 60),
    ]
    assert estimate_tokens(msgs) == int(100 * CHARS_TO_TOKENS)


def test_estimate_tokens_counts_tool_calls_meta() -> None:
    """assistant 的 tool_calls 结构也粗略计入 token。"""
    plain = Message(role="assistant", content="hi")
    with_tc = Message(
        role="assistant",
        content="hi",
        meta={"tool_calls": [{"id": "a", "name": "echo", "args": {"m": "x"}}]},
    )
    assert estimate_tokens([with_tc]) > estimate_tokens([plain])


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens([]) == 0


def test_make_budget_snapshot_derives_threshold_from_window() -> None:
    snap = make_budget_snapshot(effective_window=100_000, last_input_tokens=None)
    assert snap.effective_window == 100_000
    assert snap.output_reserve == int(100_000 * OUTPUT_RESERVE_RATIO)
    assert snap.buffer == int(100_000 * BUFFER_RATIO)
    assert snap.trigger_threshold == 100_000 - snap.output_reserve - snap.buffer


def test_threshold_scales_with_model_window() -> None:
    """AC-1.2：不同 context window → 阈值随之变化（非写死）。"""
    small = make_budget_snapshot(effective_window=8_000, last_input_tokens=None)
    large = make_budget_snapshot(effective_window=200_000, last_input_tokens=None)
    assert small.trigger_threshold < large.trigger_threshold


def test_make_budget_snapshot_keeps_anchor() -> None:
    snap = make_budget_snapshot(effective_window=8_000, last_input_tokens=1234)
    assert snap.last_input_tokens == 1234


def test_trigger_threshold_floors_at_zero() -> None:
    """预留 + 缓冲超过窗口的极端值不应得到负阈值。"""
    snap = BudgetSnapshot(
        effective_window=10,
        last_input_tokens=None,
        output_reserve=20,
        buffer=20,
    )
    assert snap.trigger_threshold == 0
