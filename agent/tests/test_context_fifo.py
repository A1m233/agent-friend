"""009 M2：``FifoContextManager`` 防爆窗兜底 单测 + AC-2.x 集成测。

单测（直接喂 ``FifoContextManager``，可控 system_prompt / 窗口，断言精确）：

- ``runtime=None`` → 退化 Naive，不裁剪
- 未超阈值 → 全留
- 超阈值 → 从最老 user 轮丢，落回预算内（预算驱动，保留 > 地板）
- 只在 user 轮边界裁剪 → 不产孤儿 tool 消息
- 保护地板：连最近 N 轮都超预算时也只裁到地板（``over_budget_after_truncation``）

集成测（Conversation + 录制 fake LLM + 真 FIFO，验 R-2.x / AC-2.x）：

- AC-2.1/2.2 长会话触发裁剪、最近输入仍在、不抛错
- AC-2.3 工具循环续轮同样经过 FIFO，超预算时裁掉更早的轮
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from agent.context import (
    RECENT_PROTECT_TURNS,
    FifoContextManager,
    RuntimeContext,
    estimate_tokens,
    make_budget_snapshot,
)
from agent.messages import Message

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    BuildResult,
    Conversation,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    PromptBuilder,
    Session,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)

# ===================== 单测辅助 =====================


def _runtime(window: int, last_input_tokens: int | None = None) -> RuntimeContext:
    """构造一个本轮预算 runtime（FIFO 不用 llm_client，塞占位对象即可）。"""
    return RuntimeContext(
        budget=make_budget_snapshot(window, last_input_tokens),
        llm_client=cast(Any, object()),
        prior_summary=None,
    )


def _turn(i: int, size: int) -> list[Message]:
    """一个普通 user 轮：user + assistant，各 ``size`` 字符填充。"""
    return [
        Message(role="user", content=f"u{i}" + "x" * size),
        Message(role="assistant", content=f"a{i}" + "x" * size),
    ]


def _history(n_turns: int, size: int) -> list[Message]:
    out: list[Message] = []
    for i in range(n_turns):
        out.extend(_turn(i, size))
    return out


def _user_count(messages: list[Message]) -> int:
    return sum(1 for m in messages if m.role == "user")


def _assert_no_orphan_tool(messages: list[Message]) -> None:
    """断言不存在孤儿 tool 消息：tool 结果的 id 必须有对应 assistant tool_call。"""
    assistant_ids: set[str] = set()
    for m in messages:
        for tc in m.meta.get("tool_calls", []) if m.meta else []:
            assistant_ids.add(tc.get("id", ""))
    for m in messages:
        if m.role == "tool":
            tcid = m.meta.get("tool_call_id", "") if m.meta else ""
            assert tcid in assistant_ids, f"孤儿 tool 消息: {tcid!r} 无对应 assistant tool_call"


def _first_history_role(result_messages: list[Message]) -> str | None:
    """跳过开头的 system 消息后，第一条历史消息的 role（用于断言不以 tool 起头）。"""
    for m in result_messages:
        if m.role != "system":
            return m.role
    return None


# ===================== 单测：FifoContextManager =====================


def test_runtime_none_degrades_to_naive() -> None:
    """runtime=None → 全发不截断（不变量 6）。"""
    history = _history(10, 200)
    cm = FifoContextManager()
    result = cm.build_messages(history=history, system_prompt="sys", runtime=None)
    assert result.dropped_count == 0
    # system + 全量 history
    assert len(result.messages) == len(history) + 1


def test_under_threshold_keeps_all() -> None:
    """未超阈值：一条不丢。"""
    history = _history(2, 50)  # 估算很小
    cm = FifoContextManager()
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(window=100_000)
    )
    assert result.dropped_count == 0
    assert len(result.messages) == len(history) + 1
    assert result.notes.get("fifo_truncated") in (None, False)


def test_fifo_passes_trailing_user() -> None:
    """021：FifoContextManager 在首拼 + 截断两条路径下都透传 trailing_user 到末尾。"""
    cm = FifoContextManager()

    # 未超阈值：trailing_user 应在末尾
    history = _history(2, 50)
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(window=100_000),
    )
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"

    # 超阈值（强制裁剪路径）：trailing_user 仍在末尾
    history = _history(6, 100)
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(window=700),
    )
    assert result.dropped_count > 0
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"


def test_over_threshold_drops_oldest_until_within_budget() -> None:
    """超阈值：丢最老轮直到落回预算内（预算驱动，保留多于地板）。"""
    # 每条 100 字符 → est 75/条；每轮 150；6 轮共 900。
    history = _history(6, 100)
    cm = FifoContextManager()
    # window=700 → threshold = 700 - 70 - 70 = 560；允许 3 轮(450) 不允许 4 轮(600)
    result = cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(window=700))

    assert result.dropped_count > 0
    assert result.notes["fifo_truncated"] is True
    assert result.notes["over_budget_after_truncation"] is False
    # 落回预算内
    assert estimate_tokens(result.messages) <= 560
    # 预算驱动：保留的轮数应多于纯地板（说明不是被地板卡住）
    assert _user_count(result.messages) > RECENT_PROTECT_TURNS
    # 丢的是最老的 → 最近一轮的 user 内容仍在
    assert any("u5" in m.content for m in result.messages)
    assert not any("u0" in m.content for m in result.messages)
    _assert_no_orphan_tool(result.messages)
    assert _first_history_role(result.messages) == "user"


def test_protection_floor_caps_truncation() -> None:
    """连最近 N 轮都超预算时，只裁到地板、不裁光（标记 over_budget）。"""
    history = _history(6, 100)  # 每轮 est 150
    cm = FifoContextManager()
    # window=100 → threshold = 100 - 10 - 10 = 80；连 1 轮(150)都放不下
    result = cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(window=100))

    assert result.dropped_count > 0
    assert result.notes["over_budget_after_truncation"] is True
    # 地板保住最近 N 轮
    assert _user_count(result.messages) == RECENT_PROTECT_TURNS
    assert any("u5" in m.content for m in result.messages)
    _assert_no_orphan_tool(result.messages)


def test_truncation_never_splits_tool_group() -> None:
    """带工具组的历史被裁时，保组完整、不以 tool 起头、无孤儿。"""
    big = "x" * 200
    history: list[Message] = [
        Message(role="user", content="u0" + big),
        Message(role="assistant", content="a0" + big),
        # turn1：assistant 发起 tool_call + tool 结果 + 最终 assistant
        Message(role="user", content="u1" + big),
        Message(
            role="assistant",
            content="",
            meta={"tool_calls": [{"id": "c1", "name": "loop", "args": {}}]},
        ),
        Message(
            role="tool",
            content="result" + big,
            meta={"tool_call_id": "c1", "tool_name": "loop", "is_error": False},
        ),
        Message(role="assistant", content="final" + big),
        # turn2
        Message(role="user", content="u2" + big),
        Message(role="assistant", content="a2" + big),
    ]
    cm = FifoContextManager()
    # 小窗口逼着丢 turn0（保护地板 = 最近 2 轮 = turn1 + turn2）
    result = cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(window=200))

    assert result.dropped_count > 0
    # turn0 被丢，turn1 的工具组完整保留
    assert not any("u0" in m.content for m in result.messages)
    assert any("u1" in m.content for m in result.messages)
    _assert_no_orphan_tool(result.messages)
    assert _first_history_role(result.messages) == "user"


def test_no_user_messages_cannot_truncate() -> None:
    """历史里没有 user 边界 → 没有可安全裁剪的点，原样返回（尽力而为）。"""
    history = [Message(role="assistant", content="x" * 500) for _ in range(3)]
    cm = FifoContextManager()
    result = cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(window=100))
    assert result.dropped_count == 0
    assert len(result.messages) == len(history) + 1


# ===================== 集成测辅助 =====================


@dataclass
class _RecordingLLM:
    """录制每次 stream 收到的 messages 的 fake LLM；script 跑完后默认 end_turn。"""

    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    context_window: int = 200
    turn_idx: int = 0
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.calls.append(messages)
        if self.turn_idx >= len(self.script):
            yield LLMTextDelta(text="ok")
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


class _RecordingFifo(FifoContextManager):
    """真 FIFO + 记录每次 build_messages 的结果（区分首轮 / 续轮）。"""

    def __init__(self) -> None:
        self.results: list[BuildResult] = []

    def build_messages(self, *args: Any, **kwargs: Any) -> BuildResult:
        result = super().build_messages(*args, **kwargs)
        self.results.append(result)
        return result


class _LoopTool:
    name: ClassVar[str] = "loop"
    description: ClassVar[str] = "总是被调用，制造工具循环。"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(text="looped" + "x" * 200)


def _seed_turns(session: Session, store: JsonlSessionStore, n: int, size: int) -> None:
    """直接把 n 个普通 user 轮 append 进 session + store（不经 LLM）。"""
    for i in range(n):
        for ev in (
            Event(
                type="user_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": f"seed-u{i}" + "x" * size},
            ),
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": f"seed-a{i}" + "x" * size, "partial": False},
                meta={"persona": "default", "model": "deepseek/deepseek-chat"},
            ),
        ):
            store.append_event(session.session_id, ev)
            session.append(ev)


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: Any,
    context_manager: Any,
    registry: ToolRegistry | None = None,
) -> tuple[Conversation, Session, JsonlSessionStore]:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t", persona="default", model="deepseek/deepseek-chat", persona_id=persona_id
    )
    store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    conv = Conversation(
        session=session,
        store=store,
        llm_client=cast(Any, fake_llm),
        context_manager=cast(Any, context_manager),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=registry,
    )
    return conv, session, store


# ===================== 集成测：AC-2.x =====================


def test_ac21_long_conversation_truncates_and_keeps_recent(tmp_path: Path) -> None:
    """AC-2.1/2.2：长会话触发 FIFO 裁剪、不抛错、最新输入仍在上下文里。"""
    fake = _RecordingLLM(context_window=200)  # 小窗口逼裁剪
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=fake, context_manager=FifoContextManager()
    )
    _seed_turns(session, store, n=5, size=60)

    # 不抛 LLMBadRequestError，对话正常完成
    reply = conv.send("最新的问题ABC")
    assert reply == "ok"

    # FIFO 已裁剪
    assert conv.last_context_notes["dropped_count"] > 0

    # 最近输入仍在最后一次发往 LLM 的消息里（AC-2.2 保留近期上下文）
    last_call = fake.calls[-1]
    assert any("最新的问题ABC" in (m.get("content") or "") for m in last_call)
    # 最老的 seed 轮已被裁掉
    assert not any("seed-u0" in (m.get("content") or "") for m in last_call)


def test_ac23_tool_loop_continuation_truncates(tmp_path: Path) -> None:
    """AC-2.3：带工具的长会话，续轮也经过 FIFO，超预算时裁掉更早的轮。"""
    fake = _RecordingLLM(
        script=[
            [
                LLMToolCallDelta(index=0, tool_call_id="c", tool_name="loop", args_json_delta="{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ],
        context_window=200,
    )
    rec = _RecordingFifo()
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=fake, context_manager=rec, registry=ToolRegistry([_LoopTool()])
    )
    _seed_turns(session, store, n=4, size=60)

    reply = conv.send("触发工具的问题")
    assert reply == "done"

    # 至少：首轮 + 一次工具续轮
    assert len(rec.results) >= 2
    # 续轮（非首轮）同样经过 FIFO 并发生了裁剪（裁掉更早的轮）
    assert any(r.dropped_count > 0 for r in rec.results[1:]), "工具续轮应经过 FIFO 裁剪"
