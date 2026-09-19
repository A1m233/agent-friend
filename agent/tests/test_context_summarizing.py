"""009 M3：摘要压缩 单测 + AC-3.x（mock LLM）。

覆盖：

- ``summary_prompt``：转录渲染 / strip_analysis / 注入前缀
- ``compaction`` 事件 round-trip + ``Session.latest_compaction`` + messages 不含 compaction
- ``SummarizingContextManager``：runtime=None 退化 / 平时折叠 / 超阈值触发摘要 /
  全量 vs 增量输入 / circuit breaker 跳闸 / 空摘要当失败 / 无从压缩退兜底
- 集成（Conversation）：触发即落 compaction、下一轮复用 prior_summary 不重摘（AC-3.4/3.5）；
  135 起失败有损降级暂停，保留历史与执行结果

真实压缩质量 / 关键信息不漂移（AC-3.1/3.2）需真 LLM，按 llm-api-confirm 另行验证。
"""

from __future__ import annotations

import json
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
    ContextBaseOverBudgetError,
    ContextCompactionUnavailableError,
    ContextInputTooLargeError,
    RuntimeContext,
    SummarizingContextManager,
    make_budget_snapshot,
)
from agent.context.checkpoints import resolve_prior_summary, summary_message
from agent.context.summary_prompt import (
    build_summary_messages,
    render_summary_as_context,
    render_transcript,
    strip_analysis,
)
from agent.messages import Message
from agent.sessions.rollout import SessionRolloutRecorder, restore_rollout
from agent.system_prompt import IM_QQ_SURFACE
from agent.tools.builtin.tool_result_recovery import ToolResultRecoveryTool
from llm_providers.request_witness import witness_from_body

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    ContextCompactionStatus,
    Conversation,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    PromptBuilder,
    Session,
    ToolRegistry,
)
from llm_providers import (
    LLMContextWindowError,
    LLMNetworkError,
    LLMResponseDiagnostics,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTransientProviderError,
    LLMTurnDone,
    LLMUnsupportedParameterError,
    LLMUsage,
    ModelRequest,
    ModelResponse,
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
    generate_requests: list[ModelRequest] = field(default_factory=list)

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        self.generate_requests.append(request)
        if self.fail:
            raise RuntimeError("boom")
        return ModelResponse(text=self.summary_text)

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
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
class _WitnessSummaryLLM(_FakeLLM):
    def generate(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        self.generate_requests.append(request)
        witness = witness_from_body(
            json.dumps({"messages": request.messages}).encode(),
            provider_id="test",
            transport="litellm",
            transform_identity="test:v1",
        )
        return ModelResponse(
            text=self.summary_text,
            usage=LLMUsage(prompt_tokens=100),
            diagnostics=LLMResponseDiagnostics(
                request_witness=witness,
                response_model="test-model",
            ),
        )


@dataclass
class _ContextRejectOnceLLM(_FakeLLM):
    stream_calls: int = 0

    def stream(
        self,
        request: ModelRequest,
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

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        self.summary_input_lengths.append(len(str(request.messages[-1]["content"])))
        if self.remaining_context_failures > 0:
            self.remaining_context_failures -= 1
            raise LLMContextWindowError("summary input too large")
        return ModelResponse(text=self.summary_text)


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
        model_id="deepseek/test",
        prior_summary=prior,
        calibration_factor=calibration_factor,
        session_id="test-session",
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
    history = _turns(12, 100)
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=_runtime(llm, window=1600),
    )
    assert result.new_compaction is not None
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"

    # 路径 3：跳闸退化到 FIFO 兜底（仍要保留 trailing_user 在末尾）
    cm = SummarizingContextManager(max_failures=1)
    cm._failure_scope = ("test-session", "run", "deepseek/test")
    fallback_runtime = _runtime(_FakeLLM(), window=100_000)
    fallback_runtime.run_id = "run"
    cm._tripped = True  # 直接强制跳闸，触发 _fallback_result 路径
    result = cm.build_messages(
        history=history,
        system_prompt="sys",
        trailing_user="ADDENDUM",
        runtime=fallback_runtime,
    )
    assert result.notes.get("fell_back") is True
    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "ADDENDUM"


def test_over_threshold_triggers_summary_and_keeps_recent_tail() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    history = _turns(12, 100)
    result = cm.build_messages(
        history=history, system_prompt="sys", runtime=_runtime(llm, window=1600)
    )

    assert llm.complete_calls == 1
    trace = llm.generate_requests[0].control.trace_context
    assert trace is not None
    assert trace.operation == "context_compaction"
    assert trace.call_kind == "summary_generation"
    assert trace.recovery_attempt == 0
    assert result.new_compaction is not None
    assert result.new_compaction.covered_through_uuid == "a9"
    assert result.new_compaction.model == "deepseek/test"
    assert result.notes["compacted"] is True
    assert result.notes["summary_input"] == "bounded_full"
    joined = _joined(result.messages)
    # 摘要前缀在、最老轮被折掉、最近 tail 逐字保留
    assert "压缩后的结构化摘要" in joined
    assert "U0-" not in joined
    assert "U11-" in joined


def test_summary_cancellation_is_forwarded_and_never_downgraded_to_fifo() -> None:
    from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled

    token = CancellationToken()

    class CancellingLLM(_FakeLLM):
        def generate(self, request: ModelRequest) -> ModelResponse:
            self.complete_calls += 1
            assert request.control.cancel_check is not None
            token.cancel(CancelReason.EXPLICIT_REQUEST)
            request.control.cancel_check()
            raise AssertionError("cancel check must interrupt")

    llm = CancellingLLM()
    runtime = _runtime(llm, window=1600)
    runtime.cancel_check = token.raise_if_cancelled
    cm = SummarizingContextManager()
    with pytest.raises(RunCancelled):
        cm.build_messages(history=_turns(12, 100), system_prompt="sys", runtime=runtime)
    assert llm.complete_calls == 1
    assert cm._consecutive_failures == 0


def test_repeated_compaction_uses_operation_scoped_exact_lineage() -> None:
    llm = _WitnessSummaryLLM()
    cm = SummarizingContextManager()

    first = cm.build_messages(
        history=_turns(12, 100),
        system_prompt="sys",
        runtime=_runtime(llm, window=1600),
    )
    second = cm.build_messages(
        history=_turns(13, 100),
        system_prompt="sys",
        runtime=_runtime(llm, window=1600),
    )

    assert first.new_compaction is not None
    assert second.new_compaction is not None
    assert len(llm.generate_requests) == 2
    first_trace = llm.generate_requests[0].control.trace_context
    second_trace = llm.generate_requests[1].control.trace_context
    assert first_trace is not None
    assert first_trace.comparison_scope == "unavailable"
    assert first_trace.comparison is not None
    assert first_trace.comparison["unavailable_reason"] == "no_previous_operation_request"
    assert second_trace is not None
    assert second_trace.comparison_scope == "within_operation"
    assert second_trace.comparison_precision == "exact_item"
    assert llm.generate_requests[1].control.previous_request_witness is not None


def test_stream_build_yields_started_before_summary_call() -> None:
    llm = _FakeLLM()
    cm = SummarizingContextManager()
    stream = cm.stream_build_messages(
        history=_turns(12, 100),
        system_prompt="sys",
        runtime=_runtime(llm, window=1600),
    )

    started = next(stream)

    assert started.phase == "started"
    assert started.activity_id
    assert llm.complete_calls == 0
    with pytest.raises(StopIteration) as stopped:
        next(stream)
    result = stopped.value.value
    assert llm.complete_calls == 1
    assert result.new_compaction is not None
    assert result.new_compaction.activity_id == started.activity_id


def test_stream_build_discards_failed_summary_activity() -> None:
    llm = _FakeLLM(fail=True)
    cm = SummarizingContextManager()
    stream = cm.stream_build_messages(
        history=_turns(12, 100),
        system_prompt="sys",
        runtime=_runtime(llm, window=1600),
    )

    started = next(stream)
    discarded = next(stream)

    assert discarded.phase == "discarded"
    assert discarded.activity_id == started.activity_id
    with pytest.raises(ContextCompactionUnavailableError):
        next(stream)


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
    history = _turns(12, 100)
    rt = _runtime(llm, window=1600)
    rt.run_id = "same-run"

    original = [(m.uuid, m.content) for m in history]
    for _ in range(4):
        with pytest.raises(ContextCompactionUnavailableError):
            cm.build_messages(history=history, system_prompt="sys", runtime=rt)
    assert llm.complete_calls == 3
    assert cm._tripped
    assert [(m.uuid, m.content) for m in history] == original


def test_empty_summary_counts_as_failure() -> None:
    llm = _FakeLLM(summary_text="<summary>   </summary>")
    cm = SummarizingContextManager()
    history = _turns(12, 100)
    with pytest.raises(ContextCompactionUnavailableError):
        cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(llm, 1600))
    assert cm._consecutive_failures == 1


@pytest.mark.parametrize("summary", ["", "x" * 10_000])
def test_unusable_summary_cannot_commit_fifo_candidate(summary: str) -> None:
    llm = _FakeLLM(summary_text=summary)
    history = _turns(12, 100)
    original = [(m.uuid, m.content) for m in history]
    with pytest.raises(ContextCompactionUnavailableError) as caught:
        SummarizingContextManager().build_messages(
            history=history, system_prompt="sys", runtime=_runtime(llm, 1600)
        )
    assert caught.value.reason == ("empty_summary" if not summary else "summary_result_over_budget")
    assert [(m.uuid, m.content) for m in history] == original


@pytest.mark.parametrize("failure", [LLMNetworkError("offline"), LLMTransientProviderError("502")])
def test_summary_outage_with_exhausted_fifo_keeps_recoverable_cause(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    llm = _FakeLLM()

    def unavailable(request: ModelRequest) -> ModelResponse:
        llm.complete_calls += 1
        raise failure

    monkeypatch.setattr(llm, "generate", unavailable)
    cm = SummarizingContextManager(max_failures=1)
    history = [
        *_turns(12, 100),
        _m("user", "continue", "current"),
        _m("assistant", "x" * 5000, "current-answer"),
    ]
    original = [(m.uuid, m.content) for m in history]
    # Real FIFO cannot discard the in-progress last turn. Repeated explicit
    # attempts must still reach the summary service, even past the breaker limit.
    for attempt in range(1, 3):
        with pytest.raises(ContextCompactionUnavailableError) as caught:
            cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(llm, 2000))
        assert caught.value.__cause__ is failure
        assert llm.complete_calls == attempt
    assert [(m.uuid, m.content) for m in history] == original


def test_summary_outage_retries_without_committing_fifo(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _FakeLLM()

    def unavailable(request: ModelRequest) -> ModelResponse:
        llm.complete_calls += 1
        raise LLMTransientProviderError("502")

    monkeypatch.setattr(llm, "generate", unavailable)
    cm = SummarizingContextManager(max_failures=1)
    history = _turns(12, 100)
    for _ in range(2):
        with pytest.raises(ContextCompactionUnavailableError):
            cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(llm, 1600))
    assert llm.complete_calls == 2


def test_summary_outage_does_not_hide_base_budget_error(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _FakeLLM()

    def unavailable(request: ModelRequest) -> ModelResponse:
        raise LLMTransientProviderError("502")

    monkeypatch.setattr(llm, "generate", unavailable)
    with pytest.raises(ContextBaseOverBudgetError):
        SummarizingContextManager().build_messages(
            history=_turns(12, 100), system_prompt="x" * 5000, runtime=_runtime(llm, 2000)
        )


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
        cm.build_messages(history=history, system_prompt="sys", runtime=_runtime(llm, window=1600))
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


def _seed_large_tool_results(
    session: Session,
    store: JsonlSessionStore,
    *,
    count: int = 9,
    size: int = 20_000,
) -> list[str]:
    result_refs: list[str] = []
    for i in range(count):
        assistant_uuid = str(uuid4())
        call_id = f"call-{i}"
        result_uuid = f"large-result-{i}"
        events = (
            Event(
                type="user_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": f"search request {i}"},
            ),
            Event(
                type="assistant_message",
                uuid=assistant_uuid,
                ts=datetime.now(UTC),
                payload={"content": "", "partial": False},
                meta={"persona": "default", "model": "deepseek/test"},
            ),
            Event(
                type="tool_call_request",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={
                    "tool_call_id": call_id,
                    "event_tool_call_id": f"event-{call_id}",
                    "tool_name": "web_search",
                    "args": {"query": f"query {i}"},
                },
            ),
            Event(
                type="tool_call_result",
                uuid=result_uuid,
                ts=datetime.now(UTC),
                payload={
                    "tool_call_id": call_id,
                    "event_tool_call_id": f"event-{call_id}",
                    "tool_name": "web_search",
                    "content": f"large-{i}-" + "x" * size,
                    "is_error": False,
                },
                meta={"extra": {"context_retention": "recoverable"}},
            ),
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": f"answer {i}", "partial": False},
                meta={"persona": "default", "model": "deepseek/test"},
            ),
        )
        for event in events:
            store.append_event(session.session_id, event)
            session.append(event)
        result_refs.append(result_uuid)
    return result_refs


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: Any,
    context_manager: Any,
    with_recovery_tool: bool = False,
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
        tool_registry=(
            ToolRegistry([ToolResultRecoveryTool(store)]) if with_recovery_tool else None
        ),
    )
    return conv, session, store


def _count_compactions(session: Session) -> int:
    return sum(1 for ev in session.events if ev.type == "compaction")


def _count_tool_context_edits(session: Session) -> int:
    return sum(1 for ev in session.events if ev.type == "tool_context_edit")


def test_compaction_model_only_boundary_survives_verified_rollout_restart(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=14_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)
    conv._session_model_history.reconcile(session)
    # Every possible cut ends at a model-only identity, as in the real incident.
    model_history: list[Message] = []
    for message in conv._session_model_history.items:
        model_history.append(message)
        if message.role == "assistant":
            model_history.append(
                Message(
                    role="system",
                    content="previous transaction stopped",
                    meta={"synthetic": "turn_aborted"},
                )
            )
    conv._session_model_history.items = model_history
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)

    assert conv.send("compress") == "ok"
    compaction = session.latest_compaction()
    assert compaction is not None
    assert compaction.payload["covered_through_uuid"] not in {
        event.uuid for event in session.active_events
    }
    assert conv.send("continue") == "ok"
    assert llm.complete_calls == 1

    reloaded = store.load(session.session_id)
    restored = restore_rollout(reloaded)
    resolved = resolve_prior_summary(
        reloaded.active_events, restored.history.snapshot(), scope="model"
    )
    assert resolved.status == "materialized"
    restarted = Conversation(
        session=reloaded,
        store=store,
        llm_client=cast(Any, llm),
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        session_model_history=restored.history,
        session_model_context=restored.model_context,
        cache_epoch=restored.cache_epoch,
        catalog=conv._catalog,
    )
    assert restarted.send("after restart") == "ok"
    assert llm.complete_calls == 1
    assert (
        sum(
            "这是压缩后的结构化摘要" in str(message.get("content", ""))
            for message in llm.received[-1]
        )
        == 1
    )


@pytest.mark.parametrize("marked", [True, False])
def test_repeated_compaction_keeps_prior_summary_in_bounded_input(marked: bool) -> None:
    llm = _FakeLLM(context_window=8_000)
    manager = SummarizingContextManager()
    remembered = summary_message(
        "EARLIER_FACT_MUST_SURVIVE", "previous-summary", "previous-checkpoint"
    )
    if not marked:
        remembered.meta.clear()
    history = [remembered, *_turns(80, 400)]
    result = manager.build_messages(history, "system", "next", runtime=_runtime(llm, 8_000))
    assert result.new_compaction is not None
    assert llm.complete_calls == 1
    assert "EARLIER_FACT_MUST_SURVIVE" in str(llm.generate_requests[0].messages[-1]["content"])
    assert result.new_compaction.omitted_before_count > 0


def test_unsupported_summary_parameter_is_blocked_only_for_same_run_and_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class RejectingLLM(_FakeLLM):
        def generate(self, request: ModelRequest) -> ModelResponse:
            self.complete_calls += 1
            raise LLMUnsupportedParameterError("private prompt must not appear")

    llm = RejectingLLM()
    manager = SummarizingContextManager()
    runtime = _runtime(llm, 2_000)
    runtime.run_id = "run-1"
    history = _turns(10, 400)

    def build() -> list[Any]:
        events = []
        with pytest.raises(ContextCompactionUnavailableError):
            for event in manager.stream_build_messages(history, "system", "next", runtime=runtime):
                events.append(event)
        return events

    with caplog.at_level(logging.WARNING, logger="agent.context.summarizing"):
        assert [item.phase for item in build()] == ["started", "discarded"]
        assert build() == []
        assert llm.complete_calls == 1
        runtime.run_id = "run-2"
        build()
        runtime.model_id = "other-model"
        build()
        assert llm.complete_calls == 3
        runtime.run_id = None
        build()
        build()
        assert llm.complete_calls == 5
    assert "sdk_validation" in caplog.text
    assert "unsupported_model_parameter" in caplog.text
    assert "private prompt must not appear" not in caplog.text
    assert manager._consecutive_failures == 0
    assert manager._tripped is False


def test_tool_context_edit_is_committed_and_reused_after_restart(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=300_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
        with_recovery_tool=True,
    )
    result_refs = _seed_large_tool_results(session, store)

    assert conv.send("触发低水位清理") == "ok"
    assert _count_tool_context_edits(session) == 1
    checkpoint = session.latest_tool_context_edit()
    assert checkpoint is not None
    assert checkpoint.payload["newly_cleared_result_uuids"] == result_refs[:6]
    assert any(
        f"result_ref={result_refs[0]}" in str(message.get("content", ""))
        for message in llm.received[-1]
    )
    raw_result = next(event for event in session.events if event.uuid == result_refs[0])
    assert str(raw_result.payload["content"]).startswith("large-0-")

    reloaded = store.load(session.session_id)
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    builder = MarkdownPromptBuilder(
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        catalog=catalog,
    )
    restarted = Conversation(
        session=reloaded,
        store=store,
        llm_client=cast(Any, llm),
        context_manager=SummarizingContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id,
            catalog=catalog,
        ),
        catalog=catalog,
        tool_registry=ToolRegistry([ToolResultRecoveryTool(store)]),
    )

    assert restarted.send("重启后继续") == "ok"
    assert _count_tool_context_edits(reloaded) == 1
    assert any(
        f"result_ref={result_refs[0]}" in str(message.get("content", ""))
        for message in llm.received[-1]
    )


def test_provider_rejection_discards_tool_edit_candidate_before_retry(tmp_path: Path) -> None:
    llm = _ContextRejectOnceLLM(context_window=300_000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
        with_recovery_tool=True,
    )
    _seed_large_tool_results(session, store)

    assert conv.send("provider 拒绝后重建") == "ok"

    assert llm.stream_calls == 2
    assert _count_tool_context_edits(session) == 1
    assert conv.last_context_notes["trigger_reason"] == "provider_context_exceeded"


def test_ac34_35_compaction_persisted_and_reused(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """AC-3.4/3.5：超阈值触发即落 compaction 事件；下一轮复用 prior_summary 不重摘。"""
    # window 14000 leaves headroom for the stable prompt plus initial model context；
    # 故第二轮复用折叠不再触发；首轮 seed 大量历史仍会越过阈值触发一次。
    llm = _FakeLLM(context_window=14_000)
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


def test_conversation_emits_completed_only_after_compaction_is_persisted(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=14_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    events = list(conv.stream("触发压缩"))
    statuses = [event for event in events if isinstance(event, ContextCompactionStatus)]
    compaction = session.latest_compaction()

    assert [event.phase for event in statuses] == ["started", "completed"]
    assert compaction is not None
    assert statuses[0].activity_id == statuses[1].activity_id == compaction.uuid


def test_summary_failure_preserves_history_and_retry_can_compact(tmp_path: Path) -> None:
    llm = _FakeLLM(context_window=14_000, fail=True)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager(max_failures=1)
    )
    _seed_turns(session, store, n=60, size=100)
    original = [event.uuid for event in session.active_events]
    for attempt in range(1, 3):
        with pytest.raises(ContextCompactionUnavailableError):
            conv.send("继续原任务")
        reloaded = store.load(session.session_id)
        assert set(original) <= {event.uuid for event in reloaded.active_events}
        assert _count_compactions(reloaded) == 0
        assert (
            sum(
                e.type == "user_message" and e.payload.get("content") == "继续原任务"
                for e in reloaded.active_events
            )
            == attempt
        )
    assert llm.complete_calls == 2
    assert not llm.received
    llm.fail = False
    assert conv.send("继续原任务") == "ok"
    assert _count_compactions(session) == 1


def test_failed_compaction_preserves_committed_rollout_and_completed_results(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(context_window=300_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)
    result_refs = _seed_large_tool_results(session, store, count=1, size=100)
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    assert conv.send("finish existing task") == "ok"
    baseline = restore_rollout(store.load(session.session_id)).history.snapshot()
    llm.context_window = 14_000
    llm.fail = True
    with pytest.raises(ContextCompactionUnavailableError):
        conv.send("next task")
    reloaded = store.load(session.session_id)
    restored = restore_rollout(reloaded)
    assert {m.uuid for m in baseline} <= {m.uuid for m in restored.history.snapshot()}
    assert set(result_refs) <= {e.uuid for e in reloaded.active_events}
    assert len(llm.received) == 1
    llm.fail = False
    restarted = Conversation(
        session=reloaded,
        store=store,
        llm_client=cast(Any, llm),
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        session_model_history=restored.history,
        session_model_context=restored.model_context,
        cache_epoch=restored.cache_epoch,
        catalog=conv._catalog,
    )
    assert restarted.send("continue next task") == "ok"
    assert len([e for e in reloaded.active_events if e.uuid in result_refs]) == 1


def test_compaction_checkpoint_is_reused_after_real_instance_restart(tmp_path: Path) -> None:
    # Leave enough fixed-prompt headroom for the post-compaction summary + recent tail.
    llm = _FakeLLM(context_window=14_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    assert conv.send("压缩前的新消息") == "ok"
    assert llm.complete_calls == 1
    compaction = session.latest_compaction()
    assert compaction is not None
    assert compaction.payload["checkpoint_version"] == 3

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
    llm = _ContextRejectOnceLLM(context_window=14_000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    _seed_turns(session, store, n=60, size=100)

    assert conv.send("触发 provider 超窗恢复") == "ok"

    assert llm.stream_calls == 2
    assert _count_compactions(session) == 1
    assert conv.last_context_notes["trigger_reason"] == "provider_context_exceeded"


def test_invalid_checkpoint_boundary_is_ignored_and_rebuilt(tmp_path: Path) -> None:
    # Leave enough fixed-prompt headroom for rebuilding a valid checkpoint.
    llm = _FakeLLM(context_window=14_000)
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
    assert latest.payload["checkpoint_version"] == 3


def test_usage_restore_projects_model_once_and_keeps_latest_active_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv, session, _store = _make_conversation(
        tmp_path, fake_llm=_FakeLLM(), context_manager=SummarizingContextManager()
    )
    current_model = session.current_model
    for index in range(200):
        session.append(
            Event(
                type="assistant_message",
                uuid=f"usage-{index}",
                ts=datetime.now(UTC),
                payload={"content": "ok"},
                meta={
                    "context_usage": {
                        "model": current_model if index % 2 == 0 else "other-model",
                        "calibration_factor": 1.5 if index % 2 == 0 else 2.0,
                        "prompt_tokens": 1000 + index,
                    }
                },
            )
        )
    session.append(
        Event(
            type="turn_rewrite",
            uuid="rewrite",
            ts=datetime.now(UTC),
            payload={"inactive_event_uuids": ["usage-198"]},
        )
    )
    reads = 0
    original = cast(property, Session.__dict__["current_model"]).fget
    assert original is not None

    def counted(instance: Session) -> str:
        nonlocal reads
        reads += 1
        return cast(str, original(instance))

    monkeypatch.setattr(Session, "current_model", property(counted))
    conv._restore_context_usage()
    assert reads == 1
    assert conv._last_input_tokens == 1196
    assert conv._calibration_by_model == {current_model: 1.5, "other-model": 2.0}


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
