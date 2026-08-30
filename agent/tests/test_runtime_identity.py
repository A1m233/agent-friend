from __future__ import annotations

from typing import cast

from agent.runtime import (
    AgentRuntime,
    RuntimeInstanceIdentity,
    RuntimeStartReason,
    UserEvent,
)

from agent import Conversation


def test_runtime_start_reason_unknown_value_fails_closed_to_direct_start() -> None:
    assert RuntimeStartReason.parse("unknown-new-reason") is RuntimeStartReason.DIRECT_START
    assert RuntimeStartReason.parse(None) is RuntimeStartReason.DIRECT_START


def test_run_snapshot_is_bound_to_runtime_instance() -> None:
    identity = RuntimeInstanceIdentity.create(
        supervisor_generation=3,
        start_reason=RuntimeStartReason.SERVICE_RECOVERY,
    )
    runtime = AgentRuntime(
        conversation_factory=lambda _session_id: cast(Conversation, None),
        runtime_identity=identity,
    )

    handle = runtime.submit(
        UserEvent(session_id="session-1", user_input="hello"),
        run_id="run-1",
    )

    assert runtime.runtime_identity is identity
    assert handle.snapshot().runtime_instance_id == identity.bridge_instance_id
