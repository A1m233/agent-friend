from __future__ import annotations

from typing import Any, cast

from agent.context import BudgetSnapshot, FifoContextManager, RuntimeContext
from agent.context.summary_prompt import render_transcript
from agent.context.turns import split_complete_turns
from agent.messages import Message


def test_synthetic_skill_user_does_not_open_a_new_turn() -> None:
    history = [
        Message(role="user", content="real user"),
        Message(
            role="user",
            content="skill instructions",
            meta={"synthetic": "skill_instructions"},
        ),
        Message(role="assistant", content="answer"),
    ]

    prefix, turns = split_complete_turns(history)

    assert prefix == []
    assert len(turns) == 1
    assert turns[0].messages == tuple(history)


def test_fifo_preserves_latest_catalog_world_state_when_old_turns_are_dropped() -> None:
    catalog = Message(
        role="system",
        content="CURRENT CATALOG",
        meta={"synthetic": "skill_catalog_state"},
        uuid="catalog-state",
    )
    history = [
        Message(role="user", content="old" + ("x" * 1_000)),
        Message(role="assistant", content="old" + ("x" * 1_000)),
        catalog,
        Message(role="user", content="recent"),
        Message(role="assistant", content="answer"),
    ]
    runtime = RuntimeContext(
        budget=BudgetSnapshot(
            effective_window=300,
            last_input_tokens=None,
            output_reserve=0,
            buffer=0,
        ),
        llm_client=cast(Any, object()),
    )

    result = FifoContextManager().build_messages(
        history=history,
        system_prompt="system",
        new_user_input="current",
        runtime=runtime,
    )

    assert result.dropped_count > 0
    assert sum(message.uuid == "catalog-state" for message in result.messages) == 1


def test_summary_transcript_excludes_catalog_and_labels_historical_skill() -> None:
    transcript = render_transcript(
        [
            Message(
                role="system",
                content="SECRET CATALOG",
                meta={"synthetic": "skill_catalog_state"},
            ),
            Message(role="user", content="real"),
            Message(
                role="user",
                content="workflow",
                meta={"synthetic": "skill_instructions"},
            ),
        ]
    )

    assert "SECRET CATALOG" not in transcript
    assert "用户: real" in transcript
    assert "[该用户轮选择的历史 Skill 指令]: workflow" in transcript
