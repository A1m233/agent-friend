"""Issue 064: output headroom, bounded repair, and unchanged history ownership."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from agent.context import ContextCompactionUnavailableError, SummarizingContextManager
from agent.context.protocol import BuildResult, RuntimeContext
from agent.context.summary_budget import summary_generation_tokens, summary_target_tokens
from agent.messages import Message
from llm_providers.errors import (
    LLMEmptyResponseError,
    LLMIncompleteResponseError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
)

from llm_providers import LanguageModelCapabilities

from .test_context_summarizing import _runtime, _turns
from .test_context_task_compaction import SummaryModel, _task
from .test_context_task_compaction import _runtime as task_runtime


def _scenario(task: bool, model: SummaryModel) -> tuple[list[Message], RuntimeContext]:
    # A reduced admission threshold models a provider capacity recovery. The
    # short summary input still has enough physical window for 16K/32K output.
    model.context_window = 200_000
    if task:
        history = _task()
        runtime = task_runtime(model, history[0])
        runtime.budget = replace(runtime.budget, emergency_ratio=0.05)
    else:
        history = _turns(12, 100)
        runtime = _runtime(model, model.context_window)
        runtime.budget = replace(runtime.budget, emergency_ratio=0.01)
    return history, runtime


def _build(history: list[Message], runtime: RuntimeContext) -> BuildResult:
    return SummarizingContextManager().build_messages(history, "system", runtime=runtime)


@pytest.mark.parametrize("task", [False, True])
@pytest.mark.parametrize("stop", ["max_tokens", "max_output_tokens", "reasoning_only"])
def test_output_exhaustion_retries_once_with_more_space(task: bool, stop: str) -> None:
    error = (
        LLMOutputBudgetExhaustedError("no visible output")
        if stop == "reasoning_only"
        else LLMIncompleteResponseError(stop)
    )
    model = SummaryModel(outcomes=[error, "<summary>完整摘要，已完成动作不得重做。</summary>"])
    history, runtime = _scenario(task, model)
    original = copy.deepcopy(history)
    result = _build(history, runtime)
    assert [r.settings["max_tokens"] for r in model.requests] == [16_384, 32_768]
    assert model.requests[0].messages == model.requests[1].messages
    assert all(r.control.retry_limit == 0 for r in model.requests)
    assert result.new_compaction is not None
    assert result.new_compaction.summary_attempts == 2
    assert result.new_compaction.summary == "完整摘要，已完成动作不得重做。"
    assert result.notes["summary_output_retry_used"]
    assert "4096 token" in model.requests[0].messages[0]["content"]
    assert "<analysis>" not in model.requests[0].messages[0]["content"]
    assert history == original


@pytest.mark.parametrize("task", [False, True])
@pytest.mark.parametrize(
    "error,reason,calls",
    [
        (LLMIncompleteResponseError("max_tokens"), "summary_output_budget_exhausted", 2),
        (LLMIncompleteResponseError("missing_terminal"), "summary_incomplete", 1),
        (LLMEmptyResponseError("empty"), "empty_summary", 1),
    ],
)
def test_failed_repair_preserves_history_and_specific_reason(
    task: bool, error: Exception, reason: str, calls: int
) -> None:
    model = SummaryModel(outcomes=[error, error, "must not be reached"])
    history, runtime = _scenario(task, model)
    original = copy.deepcopy(history)
    with pytest.raises(ContextCompactionUnavailableError) as caught:
        _build(history, runtime)
    assert caught.value.reason == reason
    assert len(model.requests) == calls
    assert history == original
    if runtime.compaction_control is not None:
        assert runtime.compaction_control.summary_text == ""
        assert runtime.compaction_control.successful_segments == 0


@pytest.mark.parametrize("task", [False, True])
def test_model_ceiling_does_not_retry_with_the_same_budget(task: bool) -> None:
    model = SummaryModel(outcomes=[LLMIncompleteResponseError("max_tokens")])
    model.capabilities = replace(model.capabilities, max_output_tokens=8192)
    history, runtime = _scenario(task, model)
    with pytest.raises(ContextCompactionUnavailableError) as caught:
        _build(history, runtime)
    assert caught.value.reason == "summary_output_budget_exhausted"
    assert [r.settings["max_tokens"] for r in model.requests] == [8192]


def test_budget_respects_remaining_window_and_calibration() -> None:
    model = SummaryModel()
    runtime = _runtime(model, 10_000, calibration_factor=2)
    messages = [{"role": "user", "content": "x" * 4000}]
    # 6K calibrated input, 1K safety buffer, 3K remaining.
    assert summary_generation_tokens(messages, runtime) == 3000
    assert summary_generation_tokens(messages, runtime, expanded=True) == 3000
    model.capabilities = LanguageModelCapabilities(max_output_tokens=2000)
    assert summary_generation_tokens(messages, runtime) == 2000
    assert summary_target_tokens(runtime) == 512
    assert summary_generation_tokens([{"content": "x" * 20_000}], runtime) == 0


@pytest.mark.parametrize("task", [False, True])
def test_generation_headroom_does_not_change_retained_tail(task: bool) -> None:
    model = SummaryModel()
    history, runtime = _scenario(task, model)
    result = _build(history, runtime)
    limited = SummaryModel(capabilities=replace(model.capabilities, max_output_tokens=4096))
    history2, runtime2 = _scenario(task, limited)
    other = _build(history2, runtime2)
    assert result.new_compaction is not None and other.new_compaction is not None
    assert result.new_compaction.covered_through_uuid == other.new_compaction.covered_through_uuid
    assert result.new_compaction.retained_from_uuid == other.new_compaction.retained_from_uuid
    assert model.requests[0].messages == limited.requests[0].messages
    assert model.requests[0].settings["max_tokens"] == 16_384
    assert limited.requests[0].settings["max_tokens"] == 4096


def test_output_repair_counts_against_task_request_limit() -> None:
    model = SummaryModel(
        outcomes=[LLMNetworkError("offline"), LLMIncompleteResponseError("max_tokens")]
    )
    history, runtime = _scenario(True, model)
    assert runtime.compaction_control is not None
    runtime.compaction_control.summary_requests = 2
    with pytest.raises(ContextCompactionUnavailableError) as caught:
        _build(history, runtime)
    assert caught.value.reason == "summary_output_budget_exhausted"
    assert len(model.requests) == 2
    assert runtime.compaction_control.summary_requests == 4


@pytest.mark.parametrize("task", [False, True])
def test_cancel_after_exhaustion_never_starts_output_repair(task: bool) -> None:
    class Cancelled(RuntimeError):
        pass

    model = SummaryModel(outcomes=[LLMIncompleteResponseError("max_tokens")])
    history, runtime = _scenario(task, model)
    original = copy.deepcopy(history)

    def check() -> None:
        if model.requests:
            raise Cancelled()

    runtime.cancel_check = check
    with pytest.raises(Cancelled):
        _build(history, runtime)
    assert len(model.requests) == 1
    assert history == original
