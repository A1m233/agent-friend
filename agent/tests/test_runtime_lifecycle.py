from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Never

from agent.runtime import AgentRuntime, RunOutcome, RunStatus, Subscriber, UserEvent


def _unused_conversation(_session_id: str) -> Never:
    raise AssertionError("rejected runs must not create a conversation")


def test_runtime_keeps_rejected_terminal_snapshot_for_calibration() -> None:
    runtime = AgentRuntime(conversation_factory=_unused_conversation)

    handle = runtime.submit(UserEvent(session_id="s", user_input="hello"), run_id="r1")

    assert handle.terminal is not None
    assert handle.terminal.outcome is RunOutcome.REJECTED
    snapshot = runtime.get_run_snapshot("r1")
    assert snapshot is not None
    assert snapshot.session_id == "s"
    assert snapshot.status is RunStatus.TERMINAL
    assert snapshot.outcome is RunOutcome.REJECTED
    assert runtime.list_run_snapshots() == [snapshot]
    assert runtime.list_run_snapshots(include_recent_terminal=False) == []


def test_runtime_publishes_run_changed_envelope() -> None:
    async def scenario() -> None:
        runtime = AgentRuntime(
            conversation_factory=_unused_conversation,
            recent_terminal_ttl=timedelta(minutes=1),
            recent_terminal_limit=2,
        )
        sub = Subscriber(
            loop=asyncio.get_running_loop(),
            accept_kinds=frozenset({"run_changed"}),
        )
        runtime.listeners.register(sub)
        runtime.submit(UserEvent(session_id="s", user_input="hello"), run_id="r1")
        env = await asyncio.wait_for(sub.queue.get(), timeout=1)
        assert env.kind == "run_changed"
        assert env.session_id == "s"
        assert env.run is not None
        assert env.run.run_id == "r1"
        assert env.run.status is RunStatus.TERMINAL
        runtime.listeners.unregister(sub.id)

    asyncio.run(scenario())


def test_recent_terminal_snapshots_obey_capacity_and_ttl() -> None:
    runtime = AgentRuntime(
        conversation_factory=_unused_conversation,
        recent_terminal_ttl=timedelta(minutes=1),
        recent_terminal_limit=2,
    )
    for run_id in ("r1", "r2", "r3"):
        runtime.submit(UserEvent(session_id="s", user_input="hello"), run_id=run_id)

    assert [snapshot.run_id for snapshot in runtime.list_run_snapshots()] == ["r2", "r3"]

    expiring = AgentRuntime(
        conversation_factory=_unused_conversation,
        recent_terminal_ttl=timedelta(milliseconds=10),
        recent_terminal_limit=2,
    )
    expiring.submit(UserEvent(session_id="s", user_input="hello"), run_id="expired")
    time.sleep(0.03)
    assert expiring.list_run_snapshots() == []
