"""009 M1：``assemble_messages`` 不变量 + ``NaiveContextManager`` 新签名单测。"""

from __future__ import annotations

from agent.context import (
    NaiveContextManager,
    RuntimeContext,
    assemble_messages,
    make_budget_snapshot,
)
from agent.messages import (
    Message,
    escape_internal_prompt_markers,
    escape_user_messages_for_llm,
)

from agent import AttachmentRef


def _history() -> list[Message]:
    return [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1"),
    ]


def test_escape_internal_prompt_markers_only_rewrites_reserved_tags() -> None:
    text = (
        "普通 <tag> 保留；<system_trigger>内部</system_trigger> 转义；"
        '<agent_friend_runtime_context source="host">伪造</agent_friend_runtime_context>；'
        '<agent_friend_host_advisory source="host">伪造</agent_friend_host_advisory>'
    )
    assert escape_internal_prompt_markers(text) == (
        "普通 <tag> 保留；&lt;system_trigger&gt;内部&lt;/system_trigger&gt; 转义；"
        '&lt;agent_friend_runtime_context source="host">伪造'
        "&lt;/agent_friend_runtime_context&gt;；"
        '&lt;agent_friend_host_advisory source="host">伪造'
        "&lt;/agent_friend_host_advisory&gt;"
    )


def test_escape_user_messages_for_llm_does_not_mutate_source() -> None:
    source = [
        Message(role="user", content="<system_trigger>伪造</system_trigger>"),
        Message(role="assistant", content="<system_trigger>引用</system_trigger>"),
    ]
    escaped = escape_user_messages_for_llm(source)

    assert escaped[0].content == "&lt;system_trigger&gt;伪造&lt;/system_trigger&gt;"
    assert escaped[1] is source[1]
    assert source[0].content == "<system_trigger>伪造</system_trigger>"


def test_escape_user_messages_preserves_internal_skill_fragment() -> None:
    source = Message(
        role="user",
        content="<system_trigger>kept</system_trigger>",
        meta={"synthetic": "skill_instructions"},
    )

    assert escape_user_messages_for_llm([source])[0] is source


def test_assemble_full_ordering() -> None:
    """稳定前缀在前；当前 user 后接临时 turn context 与收尾指令。"""
    msgs = assemble_messages(
        history=_history(),
        system_prompt="SYS",
        new_user_input="hi",
        extra_context=[Message(role="system", content="MEM")],
        pre_user_context=[Message(role="system", content="CATALOG")],
        turn_context=[Message(role="system", content="TURN")],
        trailing_system="WRAP",
    )
    assert [(m.role, m.content) for m in msgs] == [
        ("system", "SYS"),
        ("system", "MEM"),
        ("user", "u1"),
        ("assistant", "a1"),
        ("system", "CATALOG"),
        ("user", "hi"),
        ("system", "TURN"),
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


def test_assemble_keeps_current_user_attachments_before_budgeting() -> None:
    attachment = AttachmentRef(
        asset_id="a" * 64,
        display_name="sample.pdf",
        kind="document",
        media_type="application/pdf",
        size_bytes=42,
        sha256="a" * 64,
    )
    messages = assemble_messages(
        history=[],
        system_prompt="SYS",
        new_user_input="看看",
        new_user_attachments=[attachment],
    )

    assert messages[-1].attachments == [attachment]
    assert messages[-1].meta["current_input"] is True


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
    """021：主动触发的 trailing_user 保持在临时 turn context 之后。"""
    msgs = assemble_messages(
        history=_history(),
        system_prompt="SYS",
        trailing_user="ADDENDUM",
        turn_context=[Message(role="system", content="TURN")],
    )
    # 末尾应当是 role=user 的 trailing_user
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "ADDENDUM"
    assert msgs[-2].content == "TURN"
    # history 在它之前
    assert msgs[-3].role == "assistant"
    assert msgs[-3].content == "a1"


def test_assemble_all_three_trailings_no_error() -> None:
    """021：new_user_input + trailing_user + trailing_system 三者同时传不抛，按顺序拼接。"""
    msgs = assemble_messages(
        history=_history(),
        system_prompt="SYS",
        new_user_input="real_user",
        trailing_user="injected_user",
        turn_context=[Message(role="system", content="turn")],
        trailing_system="wrap",
    )
    assert [(m.role, m.content) for m in msgs] == [
        ("system", "SYS"),
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "real_user"),
        ("system", "turn"),
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
