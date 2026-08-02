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

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from agent.context import (
    ContextInputTooLargeError,
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
from agent.system_prompt import IM_QQ_SURFACE

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
from llm_providers import (
    LLMContextWindowError,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    LLMUsage,
)

# ===================== fake LLM =====================


@dataclass
class _FakeLLM:
    """同时充当主对话 stream 与摘要 complete 的 fake。"""

    context_window: int = 2000
    summary_text: str = "<summary>这是压缩后的结构化摘要</summary>"
    fail: bool = False
    prompt_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    complete_calls: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)
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
        self.received.append(messages)
        yield LLMTextDelta(text="ok")
        usage = (
            LLMUsage(
                prompt_tokens=self.prompt_tokens,
                completion_tokens=1,
                total_tokens=self.prompt_tokens + 1,
                cached_prompt_tokens=self.cached_prompt_tokens,
                cache_miss_prompt_tokens=(
                    self.prompt_tokens - self.cached_prompt_tokens
                    if self.cached_prompt_tokens is not None
                    else None
                ),
            )
            if self.prompt_tokens is not None
            else None
        )
        yield LLMTurnDone(stop_reason="end_turn", usage=usage)


@dataclass
class _ContextRejectOnceLLM(_FakeLLM):
    stream_calls: int = 0

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise LLMContextWindowError("provider says context is too large")
        yield LLMTextDelta(text="ok")
        yield LLMTurnDone(stop_reason="end_turn")


@dataclass
class _SummaryContextRetryLLM(_FakeLLM):
    remaining_context_failures: int = 2
    summary_input_lengths: list[int] = field(default_factory=list)

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        self.complete_calls += 1
        self.summary_input_lengths.append(len(str(messages[-1]["content"])))
        if self.remaining_context_failures > 0:
            self.remaining_context_failures -= 1
            raise LLMContextWindowError("summary input too large")
        return self.summary_text


def _runtime(
    llm: Any,
    window: int,
    prior: Any = None,
    last_input_tokens: int | None = None,
    calibration_factor: float = 1.0,
) -> RuntimeContext:
    return RuntimeContext(
        budget=make_budget_snapshot(window, last_input_tokens),
        llm_client=cast(Any, llm),
        prior_summary=prior,
        calibration_factor=calibration_factor,
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


def test_render_transcript_preserves_attachment_marker() -> None:
    from agent import AttachmentRef

    attachment = AttachmentRef(
        asset_id="a" * 64,
        display_name="evidence.pdf",
        kind="document",
        media_type="application/pdf",
        size_bytes=42,
        sha256="a" * 64,
    )

    text = render_transcript([Message(role="user", content="请看", attachments=[attachment])])

    assert "[附件: evidence.pdf | application/pdf]" in text


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
    history = _turns(6, 100)
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(llm, window=1000),
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
    history = _turns(6, 100)
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=1000)
    )

    assert llm.complete_calls == 1
    assert result.new_compaction is not None
    assert result.new_compaction.covered_through_uuid == "a4"
    assert result.new_compaction.model == "deepseek/test"
    assert result.notes["compacted"] is True
    assert result.notes["summary_input"] == "bounded_full"
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


def test_compaction_leaves_headroom_for_usage_calibration_and_next_turn() -> None:
    llm = _FakeLLM(summary_text="<summary>压缩摘要</summary>")
    cm = SummarizingContextManager()
    history = _turns(36, 140)
    first = cm.build_messages(
        history=history,
        system_prompt="sys",
        new_user_input="首次压缩",
        runtime=_runtime(llm, window=8000),
    )

    record = first.new_compaction
    assert record is not None
    assert record.tokens_after <= int(record.effective_window * 0.8 * 0.7)

    history.extend(
        [
            _m("user", "首次压缩", "next-u"),
            _m("assistant", "完成", "next-a"),
        ]
    )
    prior = SimpleNamespace(
        summary=record.summary,
        covered_through_uuid=record.covered_through_uuid,
    )
    resumed = cm.build_messages(
        history=history,
        system_prompt="sys",
        new_user_input="重启后继续",
        runtime=_runtime(llm, window=8000, prior=prior, calibration_factor=1.1),
    )

    assert resumed.new_compaction is None
    assert resumed.notes["folded"] is True
    assert llm.complete_calls == 1


def test_first_huge_compaction_uses_bounded_recent_source() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(6, 500)  # 较旧部分很大
    prior = SimpleNamespace(summary="老摘要", covered_through_uuid="a1")
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=2000, prior=prior)
    )
    assert result.new_compaction is not None
    assert result.notes["summary_input"] == "bounded_full"
    assert result.new_compaction.omitted_before_count > 0


def test_circuit_breaker_trips_after_max_failures() -> None:
    llm = _FakeLLM(fail=True)
    cm = SummarizingContextManager()
    history = _turns(6, 100)
    rt = _runtime(llm, window=1000)

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
    history = _turns(6, 100)
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=1000)
    )
    assert result.notes["fell_back"] is True
    assert result.notes["fallback_reason"] == "empty_summary"
    assert result.new_compaction is None


def test_summary_context_error_retries_with_smaller_complete_turn_source() -> None:
    llm = _SummaryContextRetryLLM()
    cm = SummarizingContextManager()
    history = _turns(20, 100)

    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        runtime=_runtime(llm, window=2000),
    )

    assert result.new_compaction is not None
    assert llm.complete_calls == 3
    assert llm.summary_input_lengths[0] > llm.summary_input_lengths[1]
    assert llm.summary_input_lengths[1] > llm.summary_input_lengths[2]
    assert result.new_compaction.summary_attempts == 3


def test_indivisible_current_turn_over_budget_fails_closed() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(1, 1000)
    with pytest.raises(ContextInputTooLargeError):
        cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(llm, window=1000))
    assert llm.complete_calls == 0


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


def test_ac34_35_compaction_persisted_and_reused(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """AC-3.4/3.5：超阈值触发即落 compaction 事件；下一轮复用 prior_summary 不重摘。"""
    # window 9000 → threshold 7200；折叠后的 summary + tail 低于阈值，
    # 故第二轮复用折叠不再触发；首轮 seed 大量历史仍会越过阈值触发一次。
    llm = _FakeLLM(context_window=9000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)
    caplog.set_level(logging.INFO, logger="agent.conversation")

    reply1 = conv.send("第一条新消息")
    assert reply1 == "ok"
    assert _count_compactions(session) == 1
    assert llm.complete_calls == 1

    list(conv.stream("第二条新消息", surface=IM_QQ_SURFACE))
    assert any(
        '<surface type="im" provider="qq" />' in message.get("content", "")
        for message in llm.received[-1]
    )
    assert "<interaction_context>" not in llm.summary_text
    # 复用已有摘要折叠，未再触发摘要
    assert _count_compactions(session) == 1
    assert llm.complete_calls == 1
    diagnostics = [
        record.getMessage()
        for record in caplog.records
        if "[ContextCompactionDbg]" in record.getMessage()
    ]
    assert any("new_compaction=True" in message for message in diagnostics)
    assert any(
        "prior_summary=True" in message and "folded=True" in message for message in diagnostics
    )


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


def test_compaction_checkpoint_is_reused_after_real_instance_restart(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=8000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    assert conv.send("压缩前的新消息") == "ok"
    assert llm.complete_calls == 1
    compaction = session.latest_compaction()
    assert compaction is not None
    assert compaction.payload["checkpoint_version"] == 2

    reloaded_store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    reloaded = reloaded_store.load(session.session_id)
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    restarted = Conversation(
        session=reloaded,
        store=reloaded_store,
        llm_client=cast(Any, llm),
        context_manager=SummarizingContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
    )

    assert restarted.send("应用重启后的继续消息") == "ok"
    assert llm.complete_calls == 1
    assert _count_compactions(reloaded) == 1


def test_provider_context_rejection_discards_candidate_and_commits_one_retry(
    tmp_path: Path,
) -> None:
    # Keep the fixture comfortably above the default system-prompt baseline so
    # the emergency rebuild can still create (rather than FIFO-trim past) a
    # compaction candidate after the provider rejects the first request.
    llm = _ContextRejectOnceLLM(context_window=10_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    assert conv.send("触发 provider 超窗恢复") == "ok"

    assert llm.stream_calls == 2
    assert _count_compactions(session) == 1
    assert conv.last_context_notes["trigger_reason"] == "provider_context_exceeded"


def test_invalid_checkpoint_boundary_is_ignored_and_rebuilt(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=8000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)
    invalid = Event(
        type="compaction",
        uuid="invalid-compaction",
        ts=datetime.now(UTC),
        payload={
            "checkpoint_version": 2,
            "summary_prompt_version": 1,
            "summary": "错误边界摘要",
            "summary_source_from_uuid": session.messages[0].uuid,
            "covered_through_uuid": session.messages[20].uuid,
            "retained_from_uuid": "missing-message",
        },
    )
    store.append_event(session.session_id, invalid)
    session.append(invalid)

    assert conv.send("失效 checkpoint 后继续") == "ok"

    assert llm.complete_calls == 1
    latest = session.latest_compaction()
    assert latest is not None
    assert latest.uuid != "invalid-compaction"
    assert latest.payload["checkpoint_version"] == 2


def test_usage_calibration_persists_and_restores_per_model(tmp_path: Path) -> None:
    llm = _FakeLLM(
        context_window=100_000,
        prompt_tokens=12_000,
        cached_prompt_tokens=9_000,
    )
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )

    assert conv.send("校准一次") == "ok"
    assistant = next(
        event for event in reversed(session.events) if event.type == "assistant_message"
    )
    usage = assistant.meta["context_usage"]
    assert usage["prompt_tokens"] == 12_000
    assert usage["cached_prompt_tokens"] == 9_000
    assert usage["cache_miss_prompt_tokens"] == 3_000
    assert usage["calibration_factor"] > 1.0

    reloaded = store.load(session.session_id)
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    builder = MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog)
    restarted = Conversation(
        session=reloaded,
        store=store,
        llm_client=cast(Any, llm),
        context_manager=SummarizingContextManager(),
        prompt_builder=builder,
        catalog=catalog,
    )

    assert restarted.send("校准恢复后") == "ok"
    assert restarted.last_context_notes["calibration_factor"] == pytest.approx(
        usage["calibration_factor"]
    )
