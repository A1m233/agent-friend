"""009 M1：``assemble_messages`` 不变量 + ``NaiveContextManager`` 新签名单测。"""

from __future__ import annotations

from agent.context import (
    NaiveContextManager,
    RuntimeContext,
    assemble_messages,
    make_budget_snapshot,
)
from agent.messages import Message


def _history() -> list[Message]:
    return [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1"),
    ]


def test_assemble_full_ordering() -> None:
    """顺序：system → extra_context → history → new_user → trailing_system。"""
    msgs = assemble_messages(
        history=_history(),
        system_prompt="SYS",
        new_user_input="hi",
        extra_context=[Message(role="system", content="MEM")],
        trailing_system="WRAP",
    )
    assert [(m.role, m.content) for m in msgs] == [
        ("system", "SYS"),
        ("system", "MEM"),
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "hi"),
        ("system", "WRAP"),
    ]


def test_assemble_omits_optional_parts() -> None:
    """续轮形态：无 new_user_input / extra / trailing。"""
    msgs = assemble_messages(history=_history(), system_prompt="SYS")
    assert [(m.role, m.content) for m in msgs] == [
        ("system", "SYS"),
        ("user", "u1"),
        ("assistant", "a1"),
    ]


def test_assemble_empty_system_prompt_no_leading_system() -> None:
    msgs = assemble_messages(history=[], system_prompt="", new_user_input="hi")
    assert [(m.role, m.content) for m in msgs] == [("user", "hi")]


def test_assemble_trailing_after_user() -> None:
    """trailing_system 比 new_user_input 还靠后（兜底收尾语义）。"""
    msgs = assemble_messages(
        history=[],
        system_prompt="SYS",
        new_user_input="hi",
        trailing_system="WRAP",
    )
    assert msgs[-1].role == "system"
    assert msgs[-1].content == "WRAP"
    assert msgs[-2].content == "hi"


def test_assemble_trailing_user_position() -> None:
    """021：trailing_user rendered 为 role=user，位置在 new_user_input 之后、trailing_system 之前。"""
    msgs = assemble_messages(
        history=_history(),
        system_prompt="SYS",
        trailing_user="ADDENDUM",
    )
    # 末尾应当是 role=user 的 trailing_user
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "ADDENDUM"
    # history 在它之前
    assert msgs[-2].role == "assistant"
    assert msgs[-2].content == "a1"


def test_assemble_all_three_trailings_no_error() -> None:
    """021：new_user_input + trailing_user + trailing_system 三者同时传不抛，按顺序拼接。"""
    msgs = assemble_messages(
        history=_history(),
        system_prompt="SYS",
        new_user_input="real_user",
        trailing_user="injected_user",
        trailing_system="wrap",
    )
    assert [(m.role, m.content) for m in msgs] == [
        ("system", "SYS"),
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "real_user"),
        ("user", "injected_user"),
        ("system", "wrap"),
    ]


# ===== NaiveContextManager 新签名 =====


def test_naive_build_messages_basic() -> None:
    cm = NaiveContextManager()
    result = cm.build_messages(
        history=_history(),
        system_prompt="SYS",
        new_user_input="hi",
    )
    assert result.dropped_count == 0
    assert result.new_compaction is None
    assert [(m.role, m.content) for m in result.messages] == [
        ("system", "SYS"),
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "hi"),
    ]


def test_naive_ignores_runtime() -> None:
    """Naive 收到 runtime 也不裁剪 / 不报错（行为与 001 一致）。"""
    cm = NaiveContextManager()
    runtime = RuntimeContext(
        budget=make_budget_snapshot(effective_window=10, last_input_tokens=None),
        llm_client=object(),  # type: ignore[arg-type]  # Naive 永不触碰
        prior_summary=None,
    )
    big_history = [Message(role="user", content="x" * 10000) for _ in range(20)]
    result = cm.build_messages(
        history=big_history,
        system_prompt="SYS",
        new_user_input="hi",
        runtime=runtime,
    )
    assert result.dropped_count == 0
    # system + 20 history + user
    assert len(result.messages) == 22


def test_naive_passes_trailing_user() -> None:
    """021：NaiveContextManager 透传 trailing_user 到末尾。"""
    cm = NaiveContextManager()
    result = cm.build_messages(
        history=_history(),
        system_prompt="SYS",
        trailing_user="ADDENDUM",
    )
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"
