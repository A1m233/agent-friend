"""009 M3：摘要压缩 单测 + AC-3.x（mock LLM）。

覆盖：

- ``summary_prompt``：转录渲染 / strip_analysis / 注入前缀
- ``compaction`` 事件 round-trip + ``Session.latest_compaction`` + messages 不含 compaction
- ``SummarizingContextManager``：runtime=None 退化 / 平时折叠 / 超阈值触发摘要 /
  全量 vs 增量输入 / circuit breaker 跳闸 / 空摘要当失败 / 无从压缩退兜底
- 集成（Conversation）：触发即落 compaction、下一轮复用 prior_summary 不重摘（AC-3.4/3.5）；
  连续失败跳闸退化 FIFO 不崩（AC-3.3）

真实压缩质量 / 关键信息不漂移（AC-3.1/3.2）需真 LLM，按 llm-api-confirm 另行验证。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from agent.context import (
    RuntimeContext,
    SummarizingContextManager,
    make_budget_snapshot,
)
from agent.context.summary_prompt import (
    build_summary_messages,
    render_summary_as_context,
    render_transcript,
    strip_analysis,
)
from agent.messages import Message

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    PromptBuilder,
    Session,
)
from llm_providers import LLMStreamEvent, LLMTextDelta, LLMTurnDone

# ===================== fake LLM =====================


@dataclass
class _FakeLLM:
    """同时充当主对话 stream 与摘要 complete 的 fake。"""

    context_window: int = 2000
    summary_text: str = "<summary>这是压缩后的结构化摘要</summary>"
    fail: bool = False
    complete_calls: int = 0
    spec: Any = field(default_factory=lambda: SimpleNamespace(model="deepseek/test"))

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        self.complete_calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.summary_text

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        yield LLMTextDelta(text="ok")
        yield LLMTurnDone(stop_reason="end_turn")


def _runtime(
    llm: Any, window: int, prior: Any = None, last_input_tokens: int | None = None
) -> RuntimeContext:
    return RuntimeContext(
        budget=make_budget_snapshot(window, last_input_tokens),
        llm_client=cast(Any, llm),
        prior_summary=prior,
    )


def _m(role: str, content: str, uuid: str) -> Message:
    return Message(role=cast(Any, role), content=content, uuid=uuid)


def _turns(n: int, size: int) -> list[Message]:
    """n 个普通 user 轮，uuid 形如 u0/a0/u1/a1...（user 在偶数下标）。"""
    out: list[Message] = []
    for i in range(n):
        out.append(_m("user", f"U{i}-" + "x" * size, f"u{i}"))
        out.append(_m("assistant", f"A{i}-" + "x" * size, f"a{i}"))
    return out


def _joined(messages: list[Message]) -> str:
    return "\n".join(m.content for m in messages)


# ===================== summary_prompt 单测 =====================


def test_strip_analysis_extracts_summary_block() -> None:
    raw = "<analysis>草稿时间线</analysis>\n<summary>真正的摘要</summary>"
    assert strip_analysis(raw) == "真正的摘要"


def test_strip_analysis_removes_analysis_when_no_summary_tag() -> None:
    raw = "<analysis>草稿</analysis>\n剩余正文"
    assert strip_analysis(raw) == "剩余正文"


def test_strip_analysis_passthrough_plain() -> None:
    assert strip_analysis("纯文本摘要") == "纯文本摘要"


def test_render_transcript_roles() -> None:
    msgs = [
        _m("user", "你好", "u0"),
        Message(
            role="assistant",
            content="在的",
            uuid="a0",
            meta={"tool_calls": [{"id": "c1", "name": "search", "args": {"q": "x"}}]},
        ),
        Message(role="tool", content="结果", uuid="t0", meta={"tool_name": "search"}),
    ]
    text = render_transcript(msgs)
    assert "用户: 你好" in text
    assert "AI: 在的" in text
    assert "调用工具 search" in text
    assert "[工具结果 search]: 结果" in text


def test_build_summary_messages_shape() -> None:
    msgs = build_summary_messages("一些对话")
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "一些对话" in msgs[1]["content"]


def test_render_summary_as_context_has_prefix() -> None:
    out = render_summary_as_context("摘要正文")
    assert out.endswith("摘要正文")
    assert out != "摘要正文"  # 带了前缀


# ===================== compaction 事件 / Session =====================


def test_compaction_event_roundtrip() -> None:
    ev = Event(
        type="compaction",
        uuid="comp-1",
        ts=datetime.now(UTC),
        payload={
            "summary": "S",
            "covered_through_uuid": "a3",
            "tokens_before": 100,
            "tokens_after": 20,
            "model": "deepseek/test",
        },
    )
    restored = Event.from_jsonl(ev.to_jsonl())
    assert restored.type == "compaction"
    assert restored.payload["covered_through_uuid"] == "a3"


def test_latest_compaction_and_messages_exclude_it() -> None:
    session = Session.new(title="t", persona="default", model="m")
    u = Event(type="user_message", uuid="u1", ts=datetime.now(UTC), payload={"content": "hi"})
    session.append(u)
    assert session.latest_compaction() is None

    c1 = Event(
        type="compaction",
        uuid="c1",
        ts=datetime.now(UTC),
        payload={"summary": "S1", "covered_through_uuid": "u1"},
    )
    c2 = Event(
        type="compaction",
        uuid="c2",
        ts=datetime.now(UTC),
        payload={"summary": "S2", "covered_through_uuid": "u1"},
    )
    session.append(c1)
    session.append(c2)

    latest = session.latest_compaction()
    assert latest is not None
    assert latest.uuid == "c2"  # 最近一条
    # compaction 不参与 messages 投影
    assert all(m.role != "system" or "S1" not in m.content for m in session.messages)
    assert [m.uuid for m in session.messages] == ["u1"]


# ===================== SummarizingContextManager 单测 =====================


def test_runtime_none_degrades_to_naive() -> None:
    cm = SummarizingContextManager()
    history = _turns(5, 100)
    result = cm.build_messages(history=history, system_prompt="sys", runtime=None)
    assert result.new_compaction is None
    assert len(result.messages) == len(history) + 1


def test_under_threshold_no_compaction() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(2, 20)
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=100_000)
    )
    assert result.new_compaction is None
    assert llm.complete_calls == 0
    assert result.notes.get("compacted") in (None, False)


def test_summarizing_passes_trailing_user() -> None:
    """021：SummarizingContextManager 在折叠 / 摘要 / 兜底三条路径下均透传 trailing_user 到末尾。"""
    # 路径 1：未触发摘要（折叠 / 直接返回）
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(2, 20)
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(llm, window=100_000),
    )
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"

    # 路径 2：触发摘要（仍要保留 trailing_user 在末尾）
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(6, 60)
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(llm, window=100),
    )
    assert result.new_compaction is not None
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"

    # 路径 3：跳闸退化到 FIFO 兜底（仍要保留 trailing_user 在末尾）
    cm = SummarizingContextManager(max_failures=1)
    cm._tripped = True  # 直接强制跳闸，触发 _fallback_result 路径
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(_FakeLLM(), window=100_000),
    )
    assert result.notes.get("fell_back") is True
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"


def test_over_threshold_triggers_summary_and_keeps_recent_tail() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(6, 60)  # users u0..u5；recent_tail=2 → cut 在 u4(idx8)，older 到 a3
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=100)
    )

    assert llm.complete_calls == 1
    assert result.new_compaction is not None
    assert result.new_compaction.covered_through_uuid == "a3"
    assert result.new_compaction.model == "deepseek/test"
    assert result.notes["compacted"] is True
    assert result.notes["summary_input"] == "full"
    joined = _joined(result.messages)
    # 摘要前缀在、最老轮被折掉、最近 tail 逐字保留
    assert "压缩后的结构化摘要" in joined
    assert "U0-" not in joined
    assert "U5-" in joined


def test_prior_summary_folds_without_resummarizing() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(6, 20)
    prior = SimpleNamespace(summary="老摘要内容", covered_through_uuid="a2")
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        runtime=_runtime(llm, window=100_000, prior=prior),
    )
    assert llm.complete_calls == 0  # 未超阈值，不重摘
    assert result.new_compaction is None
    assert result.notes["folded"] is True
    joined = _joined(result.messages)
    assert "老摘要内容" in joined
    assert "U0-" not in joined  # 已被折叠覆盖
    assert "U3-" in joined  # a2 之后的逐字保留


def test_extreme_degradation_uses_incremental_input() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(6, 200)  # 较旧部分很大
    prior = SimpleNamespace(summary="老摘要", covered_through_uuid="a1")
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        runtime=_runtime(llm, window=50, prior=prior),
    )
    assert result.new_compaction is not None
    assert result.notes["summary_input"] == "incremental"


def test_circuit_breaker_trips_after_max_failures() -> None:
    llm = _FakeLLM(fail=True)
    cm = SummarizingContextManager()
    history = _turns(6, 60)
    rt = _runtime(llm, window=100)

    # 前 3 次摘要失败 → 退 FIFO 兜底，不崩
    for _ in range(3):
        result = cm.build_messages(history=history, system_prompt="sys", runtime=rt)
        assert result.notes["fell_back"] is True
    assert llm.complete_calls == 3
    assert result.notes["circuit_tripped"] is True

    # 跳闸后直接走 FIFO，不再调 complete
    result4 = cm.build_messages(history=history, system_prompt="sys", runtime=rt)
    assert llm.complete_calls == 3
    assert result4.notes["fallback_reason"] == "circuit_tripped"


def test_empty_summary_counts_as_failure() -> None:
    llm = _FakeLLM(summary_text="<summary>   </summary>")
    cm = SummarizingContextManager()
    history = _turns(6, 60)
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=100)
    )
    assert result.notes["fell_back"] is True
    assert result.notes["fallback_reason"] == "empty_summary"
    assert result.new_compaction is None


def test_nothing_to_compact_falls_back_without_counting_failure() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(2, 200)  # 只有 2 轮（<= recent_tail）→ 无较旧部分可压
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=50)
    )
    assert llm.complete_calls == 0
    assert result.notes["fallback_reason"] == "nothing_to_compact"
    assert result.notes["circuit_tripped"] is False


# ===================== 集成（Conversation）=====================


def _seed_turns(session: Session, store: JsonlSessionStore, n: int, size: int) -> None:
    for i in range(n):
        for ev in (
            Event(
                type="user_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": f"seed-u{i}-" + "x" * size},
            ),
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": f"seed-a{i}-" + "x" * size, "partial": False},
                meta={"persona": "default", "model": "deepseek/test"},
            ),
        ):
            store.append_event(session.session_id, ev)
            session.append(ev)


def _make_conversation(
    tmp_path: Path, *, fake_llm: Any, context_manager: Any
) -> tuple[Conversation, Session, JsonlSessionStore]:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t", persona="default", model="deepseek/test", persona_id=persona_id
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
    )
    return conv, session, store


def _count_compactions(session: Session) -> int:
    return sum(1 for ev in session.events if ev.type == "compaction")


def test_ac34_35_compaction_persisted_and_reused(tmp_path: Path) -> None:
    """AC-3.4/3.5：超阈值触发即落 compaction 事件；下一轮复用 prior_summary 不重摘。"""
    # window 8000 → threshold 6400；system prompt(~1359) + 折叠后 summary + tail 远低于阈值，
    # 故第二轮复用折叠不再触发；首轮 seed 大量历史越过阈值触发一次。
    llm = _FakeLLM(context_window=8000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    reply1 = conv.send("第一条新消息")
    assert reply1 == "ok"
    assert _count_compactions(session) == 1
    assert llm.complete_calls == 1

    reply2 = conv.send("第二条新消息")
    assert reply2 == "ok"
    # 复用已有摘要折叠，未再触发摘要
    assert _count_compactions(session) == 1
    assert llm.complete_calls == 1


def test_ac33_circuit_breaker_degrades_without_crash(tmp_path: Path) -> None:
    """AC-3.3：摘要连续失败跳闸退化 FIFO，对话不中断、不崩。"""
    llm = _FakeLLM(context_window=8000, fail=True)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    for _ in range(4):
        assert conv.send("x") == "ok"

    # 摘要一直失败，但对话每轮都正常完成；3 次后跳闸不再调 complete
    assert llm.complete_calls == 3
    assert _count_compactions(session) == 0
