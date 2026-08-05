"""040：陈旧 tool result 的低水位上下文投影。"""

from __future__ import annotations

from typing import Any, cast

from agent.context import (
    FifoContextManager,
    PriorToolContextEdit,
    RuntimeContext,
    make_budget_snapshot,
)
from agent.context.tool_projection import project_tool_results
from agent.messages import Message


def _runtime(
    *,
    prior: PriorToolContextEdit | None = None,
    recovery_available: bool = True,
) -> RuntimeContext:
    return RuntimeContext(
        budget=make_budget_snapshot(100_000, None),
        llm_client=cast(Any, object()),
        prior_tool_context_edit=prior,
        tool_result_recovery_available=recovery_available,
    )


def _tool(index: int, *, retention: str = "recoverable", size: int = 8_000) -> Message:
    return Message(
        role="tool",
        content=f"result-{index}-" + "x" * size,
        uuid=f"result-{index}",
        meta={
            "tool_name": "web_search",
            "tool_call_id": f"call-{index}",
            "context_retention": retention,
        },
    )


def _tool_batch(batch: int, *result_indices: int, size: int = 8_000) -> list[Message]:
    tool_calls = [
        {"id": f"call-{index}", "name": "web_search", "args": {}} for index in result_indices
    ]
    return [
        Message(
            role="assistant",
            content="",
            uuid=f"assistant-{batch}",
            meta={"tool_calls": tool_calls},
        ),
        *[_tool(index, size=size) for index in result_indices],
    ]


def test_low_water_clears_oldest_recoverable_results_without_mutating_raw_history() -> None:
    history = [Message(role="user", content="old turn", uuid="user-old")]
    history.extend(_tool(index) for index in range(9))
    raw_contents = [message.content for message in history]

    result = FifoContextManager().build_messages(
        history=history,
        system_prompt="sys",
        new_user_input="new turn",
        runtime=_runtime(),
    )

    edit = result.new_tool_context_edit
    assert edit is not None
    assert edit.newly_cleared_result_uuids == tuple(f"result-{i}" for i in range(6))
    assert edit.cleared_tokens >= 16_000
    projected = {message.uuid: message for message in result.messages if message.role == "tool"}
    assert "result_ref=result-0" in projected["result-0"].content
    assert "recover_tool_result" in projected["result-0"].content
    assert projected["result-6"].content.startswith("result-6-")
    assert [message.content for message in history] == raw_contents


def test_low_water_keeps_pinned_and_current_turn_results() -> None:
    old_history: list[Message] = [Message(role="user", content="old", uuid="old-user")]
    old_history.extend(_tool(index) for index in range(5))
    old_history.append(_tool(5, retention="pinned", size=20_000))
    old_history.append(Message(role="user", content="current", uuid="current-user"))
    old_history.extend(_tool(index) for index in range(6, 11))

    result = FifoContextManager().build_messages(
        history=old_history,
        system_prompt="sys",
        runtime=_runtime(),
    )

    projected = {message.uuid: message for message in result.messages if message.role == "tool"}
    assert projected["result-5"].content.startswith("result-5-")
    assert all(
        projected[f"result-{index}"].content.startswith(f"result-{index}-")
        for index in range(6, 11)
    )
    assert result.new_tool_context_edit is not None
    assert set(result.new_tool_context_edit.newly_cleared_result_uuids) <= {
        f"result-{index}" for index in range(5)
    }


def test_low_water_keeps_current_turn_plus_three_recent_historical_batches() -> None:
    history: list[Message] = [Message(role="user", content="old", uuid="old-user")]
    for batch, result_indices in enumerate(((0, 1), (2, 3), (4, 5), (6, 7), (8, 9))):
        history.extend(_tool_batch(batch, *result_indices, size=16_000))
    history.append(Message(role="user", content="current", uuid="current-user"))
    history.extend(_tool_batch(5, 10, 11, 12, 13, 14, size=16_000))

    messages, projection = project_tool_results(history, _runtime())

    projected = {message.uuid: message for message in messages if message.role == "tool"}
    assert projection.new_edit is not None
    assert projection.new_edit.policy_version == 2
    assert set(projection.new_edit.newly_cleared_result_uuids) == {
        "result-0",
        "result-1",
        "result-2",
        "result-3",
    }
    assert all(
        projected[f"result-{index}"].content.startswith(f"result-{index}-")
        for index in range(4, 15)
    )


def test_prior_edit_reapplies_stable_placeholders_without_new_checkpoint() -> None:
    history = [Message(role="user", content="old", uuid="user-old")]
    history.extend(_tool(index) for index in range(6))
    prior = PriorToolContextEdit(cleared_result_uuids=("result-0", "result-1"))

    result = FifoContextManager().build_messages(
        history=history,
        system_prompt="sys",
        new_user_input="continue",
        runtime=_runtime(prior=prior),
    )

    projected = {message.uuid: message for message in result.messages if message.role == "tool"}
    assert "result_ref=result-0" in projected["result-0"].content
    assert "result_ref=result-1" in projected["result-1"].content
    assert result.new_tool_context_edit is None


def test_new_edit_is_disabled_when_recovery_tool_is_unavailable() -> None:
    history = [Message(role="user", content="old", uuid="user-old")]
    history.extend(_tool(index) for index in range(9))

    result = FifoContextManager().build_messages(
        history=history,
        system_prompt="sys",
        new_user_input="continue",
        runtime=_runtime(recovery_available=False),
    )

    assert result.new_tool_context_edit is None
    assert all(
        "tool result cleared" not in message.content
        for message in result.messages
        if message.role == "tool"
    )
