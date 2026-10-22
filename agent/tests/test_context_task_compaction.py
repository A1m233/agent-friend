"""Task-internal replacement, bounded summarization and checkpoint identity."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest
from agent.context import ContextCompactionUnavailableError, SummarizingContextManager
from agent.context.budget import estimate_tokens, make_budget_snapshot
from agent.context.checkpoints import (
    bind_business_boundary,
    compaction_payload,
    resolve_prior_summary,
)
from agent.context.protocol import CompactionControl, RuntimeContext
from agent.context.summary_prompt import render_transcript
from agent.context.task_compaction import _fingerprint
from agent.context.turns import is_real_user_message, safe_compaction_boundaries
from agent.context.usage_anchor import InputEstimate
from agent.messages import Message
from agent.sessions import Event
from agent.sessions.model_history import SessionModelHistory
from llm_providers.error_normalization import map_provider_error
from llm_providers.responses.stream import _ResponseFailure
from llm_providers.stream_events import ProviderAssistantTurn

from llm_providers import (
    LanguageModelCapabilities,
    LLMContextWindowError,
    LLMNetworkError,
    LLMStreamEvent,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
)


@dataclass
class SummaryModel:
    context_window: int = 10_000
    capabilities: LanguageModelCapabilities = field(
        default_factory=lambda: LanguageModelCapabilities(
            input_usage_scope="request", context_checkpoint_mode="replace_complete_groups"
        )
    )
    outcomes: list[str | Exception] = field(default_factory=list)
    requests: list[ModelRequest] = field(default_factory=list)

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("summary must use the cancellable stream")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.requests.append(request)
        result = (
            self.outcomes.pop(0)
            if self.outcomes
            else "<summary>目标不变；保留结果引用和未知状态。</summary>"
        )
        if isinstance(result, Exception):
            raise result
        yield LLMTurnDone(
            stop_reason="end_turn", assistant_turn=ProviderAssistantTurn(content=result)
        )


def _task(groups: int = 4, size: int = 1700) -> list[Message]:
    messages = [Message(role="user", uuid="user", content="检查记录；不得再次执行已完成动作")]
    for index in range(groups):
        messages.extend(
            [
                Message(
                    role="assistant",
                    uuid=f"assistant-{index}",
                    content=f"plan-{index}",
                    meta={
                        "assistant_response_id": f"response-{index}",
                        "provider_reasoning_state": {"opaque": f"state-{index}"},
                        "tool_calls": [
                            {"id": f"call-{index}", "name": "read", "arguments_json": '{"page":1}'}
                        ],
                    },
                ),
                Message(
                    role="tool",
                    uuid=f"result-{index}",
                    content=f"evidence-{index}:" + "x" * size,
                    meta={"tool_call_id": f"call-{index}", "tool_name": "read"},
                ),
            ]
        )
    return messages


def _runtime(model: SummaryModel, user: Message, *, mode: str = "automatic") -> RuntimeContext:
    def assess(messages: list[Message]) -> InputEstimate:
        if any(message.meta.get("provider_reasoning_state") for message in messages):
            return InputEstimate(
                11_000, "usage_anchor_partial", "retained_opaque", is_upper_bound=True
            )
        return InputEstimate(estimate_tokens(messages), "full_estimate", "rewritten_candidate")

    return RuntimeContext(
        budget=make_budget_snapshot(model.context_window, 9500),
        llm_client=model,
        model_id="test",
        request_assessment=assess,
        compaction_control=CompactionControl(mode=mode, current_user=user),  # type: ignore[arg-type]
    )


def test_safe_cuts_keep_parallel_calls_and_response_items_together() -> None:
    history = [
        Message(role="user", content="goal"),
        Message(role="assistant", content="thinking", meta={"assistant_response_id": "response"}),
        Message(
            role="assistant",
            content="",
            meta={"assistant_response_id": "response", "tool_calls": [{"id": "a"}]},
        ),
        Message(
            role="assistant",
            content="",
            meta={"assistant_response_id": "response", "tool_calls": [{"id": "b"}]},
        ),
        Message(role="tool", content="B", meta={"tool_call_id": "b"}),
        Message(role="tool", content="A", meta={"tool_call_id": "a"}),
        Message(role="assistant", content="tail"),
    ]
    assert safe_compaction_boundaries(history) == [1, 6, 7]
    with pytest.raises(ValueError, match="tool_transaction_pending"):
        safe_compaction_boundaries(history[:5])
    with pytest.raises(ValueError, match="orphan_tool_result"):
        safe_compaction_boundaries(history[4:])


@pytest.mark.parametrize("legacy_uuid", [False, True])
def test_one_user_task_can_replace_opaque_groups_and_pin_source(legacy_uuid: bool) -> None:
    model = SummaryModel()
    history = _task()
    user = copy.deepcopy(history[0])
    if legacy_uuid:
        history[0] = replace(history[0], uuid="legacy-wire-user")
    before = copy.deepcopy(history)
    runtime = _runtime(model, user)
    result = SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    record = result.new_compaction
    assert record is not None and record.checkpoint_version == 4
    assert record.omitted_before_count == 0
    assert history == before
    anchor = next(
        message for message in result.messages if message.meta.get("synthetic") == "task_anchor"
    )
    assert anchor.uuid != user.uuid and anchor.uuid == record.task_anchor_uuid
    assert anchor.meta["source_user_event_uuid"] == user.uuid
    assert user.content in anchor.content
    assert not is_real_user_message(anchor)
    assert not any(message.meta.get("provider_reasoning_state") for message in result.messages)
    SessionModelHistory.validate_provider_tool_history(
        [message.to_openai() for message in result.messages]
    )
    assert model.requests and all(
        request.tools is None and request.control.retry_limit == 0 for request in model.requests
    )
    assert result.notes["task_internal_compaction"]


def test_new_user_input_is_not_duplicated_as_a_goal_anchor() -> None:
    model = SummaryModel()
    history = _task()
    user = Message(role="user", uuid="new-user", content="改为只检查第二页")
    result = SummarizingContextManager().build_messages(
        history, "sys", new_user_input=user.content, runtime=_runtime(model, user)
    )
    assert result.new_compaction is not None
    assert not any(message.meta.get("synthetic") == "task_anchor" for message in result.messages)
    assert [message.content for message in result.messages if message.role == "user"] == [
        user.content
    ]


def test_pending_transaction_and_unknown_adapter_never_start_a_summary() -> None:
    history = _task()
    model = SummaryModel()
    with pytest.raises(ContextCompactionUnavailableError) as error:
        SummarizingContextManager().build_messages(
            history[:-1], "sys", runtime=_runtime(model, history[0])
        )
    assert error.value.reason == "tool_transaction_pending"
    model.capabilities = LanguageModelCapabilities()
    with pytest.raises(ContextCompactionUnavailableError) as error:
        SummarizingContextManager().build_messages(
            history, "sys", runtime=_runtime(model, history[0])
        )
    assert error.value.reason == "checkpoint_unsupported"
    assert not model.requests


def test_sequential_summary_covers_every_source_group_with_one_shared_budget() -> None:
    model = SummaryModel()
    history = _task(groups=9)
    runtime = _runtime(model, history[0])
    result = SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    assert 1 < len(model.requests) <= 3
    transcripts = "\n".join(str(request.messages) for request in model.requests)
    for index in range(9):
        assert f"evidence-{index}:" in transcripts
    assert "此前已归纳的连续前缀" in str(model.requests[1].messages)
    assert result.new_compaction is not None and result.new_compaction.omitted_before_count == 0
    assert runtime.compaction_control is not None
    runtime.compaction_control.summary_requests = 4
    with pytest.raises(ContextCompactionUnavailableError) as error:
        SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    assert error.value.reason == "compaction_no_progress"


def test_summary_provider_input_separates_host_task_from_user_goal_and_history() -> None:
    previous = "此前已验证 evidence-0；其余状态以原文结果为准。"
    model = SummaryModel(outcomes=[f"<summary>{previous}</summary>"])
    history = _task(groups=9)
    history[0] = replace(history[0], content="把工作日志压缩成简报，保留执行结果")

    result = SummarizingContextManager().build_messages(
        history, "sys", runtime=_runtime(model, history[0])
    )

    assert result.new_compaction is not None
    assert result.new_compaction.summary_prompt_version == 6
    assert 1 < len(model.requests) <= 3
    for request in model.requests:
        assert [message["role"] for message in request.messages] == ["system", "user"]
        system, source = (message["content"] for message in request.messages)
        assert "“当前任务”仅指资料内" in system
        assert "目标来源的重复展示不是新增事件" in system
        assert "前缀未见结果不代表未执行" in system
        assert source.startswith(
            "【资料分区：当前业务目标来源】\n用户: 把工作日志压缩成简报，保留执行结果"
        )
        assert "source_message_ref=user" in source
        assert "【资料分区：接续的原始记录】" in source
        assert "请严格按系统指令里的两段式格式输出" not in source
        assert "不得由旧话题替代" not in source
        assert "必须继续保留其事实与来源" not in source
    assert (
        f"【资料分区：此前已归纳的连续前缀】\n{previous}"
        in model.requests[1].messages[1]["content"]
    )


def test_capacity_repartition_and_transient_retry_share_four_requests() -> None:
    model = SummaryModel(outcomes=[LLMNetworkError("offline"), LLMContextWindowError("too large")])
    history = _task(groups=12)
    runtime = _runtime(model, history[0])
    with pytest.raises(ContextCompactionUnavailableError) as error:
        SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    assert error.value.reason in {"summary_attempt_limit", "summary_segment_limit"}
    assert len(model.requests) == 4
    assert runtime.compaction_control is not None
    assert runtime.compaction_control.summary_requests == 4


@pytest.mark.parametrize("repeated", [False, True])
@pytest.mark.parametrize("code", ["request_timeout", "fixture_unknown_error"])
def test_provider_stream_timeout_has_one_summary_retry_and_preserves_history(
    code: str, repeated: bool
) -> None:
    error = map_provider_error(_ResponseFailure({"type": "error", "code": code}, None))
    model = SummaryModel(outcomes=[error, error] if repeated else [error])
    history = _task(groups=2)
    before = copy.deepcopy(history)
    runtime = _runtime(model, history[0])
    if code == "request_timeout" and not repeated:
        result = SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
        assert result.new_compaction is not None
        assert result.new_compaction.summary_attempts == 2
    else:
        with pytest.raises(ContextCompactionUnavailableError) as caught:
            SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
        assert caught.value.reason == "llm_error"
        assert caught.value.__cause__ is error
    assert history == before
    assert len(model.requests) == (2 if code == "request_timeout" else 1)
    assert all(request.control.retry_limit == 0 for request in model.requests)
    if len(model.requests) == 2:
        assert model.requests[0].messages == model.requests[1].messages


@pytest.mark.parametrize(
    "output,reason", [("", "empty_summary"), ("x" * 20_000, "summary_result_over_budget")]
)
def test_invalid_summary_never_replaces_source(output: str, reason: str) -> None:
    model = SummaryModel(outcomes=[output])
    history = _task(groups=2)
    before = copy.deepcopy(history)
    with pytest.raises(ContextCompactionUnavailableError) as error:
        SummarizingContextManager().build_messages(
            history, "sys", runtime=_runtime(model, history[0])
        )
    assert error.value.reason == reason
    assert history == before


def test_same_input_does_not_get_a_second_automatic_summary_budget() -> None:
    model = SummaryModel()
    history = _task(groups=2)
    runtime = _runtime(model, history[0])
    SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    count = len(model.requests)
    with pytest.raises(ContextCompactionUnavailableError) as error:
        SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    assert error.value.reason == "compaction_no_progress"
    assert len(model.requests) == count


@pytest.mark.parametrize("scheduled", [False, True])
def test_v4_materialized_checkpoint_preserves_anchor_and_rejects_source_drift(
    scheduled: bool,
) -> None:
    model = SummaryModel()
    history = _task(groups=2)
    source = {"kind": "scheduled_task", "task_id": "task-1", "task_name": "检查记录"}
    if scheduled:
        history[0].meta["request_source"] = source
    runtime = _runtime(model, history[0])
    result = SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    assert result.new_compaction is not None
    events = [
        Event(
            type="user_message",
            uuid="user",
            ts=datetime.now(UTC),
            payload={
                "content": history[0].content,
                **({"request_source": source} if scheduled else {}),
            },
        ),
        *[
            Event(
                type="tool_call_result",
                uuid=message.uuid,
                ts=datetime.now(UTC),
                payload={"content": message.content},
            )
            for message in history
            if message.role == "tool"
        ],
    ]
    record = bind_business_boundary(result.new_compaction, history, events)
    checkpoint = Event(
        type="compaction",
        uuid=record.activity_id,
        ts=datetime.now(UTC),
        payload=compaction_payload(record),
    )
    events.append(checkpoint)
    restored = [Message.from_dict(message.to_dict()) for message in result.messages[1:]]
    resolution = resolve_prior_summary(events, restored, scope="model")
    assert resolution.status == "materialized"
    assert resolution.prior_summary is not None
    assert resolution.prior_summary.task_anchor is not None
    if scheduled:
        assert (
            "agent_friend_request_source"
            in resolution.prior_summary.task_anchor.to_openai()["content"]
        )
        assert any(
            "agent_friend_request_source" in str(request.messages) for request in model.requests
        )
    count = len(model.requests)
    cold_runtime = _runtime(model, history[0])
    cold_runtime.prior_summary = resolution.prior_summary
    assert (
        SummarizingContextManager()
        .build_messages(restored, "sys", runtime=cold_runtime)
        .new_compaction
        is None
    )
    assert len(model.requests) == count
    missing_anchor = [message for message in restored if message.uuid != record.task_anchor_uuid]
    assert resolve_prior_summary(events, missing_anchor, scope="model").prior_summary is None
    edited_events = [replace(events[0], payload={"content": "changed"}), *events[1:]]
    assert (
        resolve_prior_summary(edited_events, restored, scope="model").invalid_reason
        == "task_anchor_source_mismatch"
    )
    assert resolve_prior_summary(events[1:], restored, scope="model").prior_summary is None


def test_transcript_keeps_actual_tool_arguments_and_refs() -> None:
    history = _task(groups=1, size=1)
    transcript = render_transcript(history)
    assert '{"page":1}' in transcript
    assert "result_ref=result-0" in transcript
    assert "tool_call_id=call-0" in transcript
    assert "source_message_ref=user" in transcript


def test_cancellation_discards_all_in_memory_summary_segments() -> None:
    class Cancelled(RuntimeError):
        pass

    model = SummaryModel()
    history = _task(groups=8)
    runtime = _runtime(model, history[0])
    before = copy.deepcopy(history)

    def cancelled() -> None:
        if model.requests:
            raise Cancelled()

    runtime.cancel_check = cancelled
    with pytest.raises(Cancelled):
        SummarizingContextManager().build_messages(history, "sys", runtime=runtime)
    assert history == before
    assert len(model.requests) == 1
    assert (
        runtime.compaction_control is not None
        and runtime.compaction_control.successful_segments == 0
    )


def test_safe_upper_bound_below_threshold_does_not_force_compaction() -> None:
    model = SummaryModel()
    history = _task(groups=1, size=1)
    runtime = _runtime(model, history[0])
    runtime.request_assessment = lambda _: InputEstimate(
        100, "usage_anchor_partial", "bound", is_upper_bound=True
    )
    assert (
        SummarizingContextManager().build_messages(history, "sys", runtime=runtime).new_compaction
        is None
    )
    assert not model.requests


def test_candidate_progress_ignores_activity_uuids_but_counts_effective_input() -> None:
    first = [Message(role="system", content="same summary", meta={"compaction_id": "first"})]
    other_activity = [replace(first[0], uuid="other-uuid", meta={"compaction_id": "second"})]
    assert _fingerprint(first) == _fingerprint(other_activity)
    assert _fingerprint(first) != _fingerprint([replace(first[0], content="changed summary")])
    assert _fingerprint(first) != _fingerprint(
        [replace(first[0], meta={"provider_reasoning_state": {"opaque": "changed"}})]
    )
