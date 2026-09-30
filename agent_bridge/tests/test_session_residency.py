from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.runtime import SessionBusyError
from agent.sessions.residency import SessionResidency
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.session_residency import sweep
from agent_bridge.session_turns import SessionTurnCoordinator


@pytest.mark.parametrize(
    "protection", ["queued", "approval", "command", "transaction", "reservation", "turn"]
)
def test_sweep_respects_backend_activity_and_mutation_boundaries(protection: str) -> None:
    released: list[str] = []
    state = object()

    def release(sid: str, expected: object) -> bool:
        assert expected is state
        released.append(sid)
        return True

    @contextmanager
    def reserve(sid: str) -> Iterator[None]:
        if protection == "reservation":
            raise SessionBusyError("busy")
        yield

    coordinator = SessionTurnCoordinator()
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            persistent_session_manager=SimpleNamespace(
                resident_states=lambda: {"s": state},
                resident_size=lambda _: 1024,
                resident_revision=lambda _: 1,
                release_idle_runtime=release,
                has_unresolved_tool_transactions=lambda _: protection == "transaction",
            ),
            agent_runtime=SimpleNamespace(
                list_run_snapshots=lambda **_: [object()] if protection == "queued" else [],
                try_reserve_session_mutation=reserve,
            ),
            approval_broker=SimpleNamespace(
                pending_for_session=lambda _: [object()] if protection == "approval" else []
            ),
            command_runtime=SimpleNamespace(
                snapshots=lambda **_: (
                    [SimpleNamespace(terminal=lambda: False)] if protection == "command" else []
                )
            ),
            turn_coordinator=coordinator,
            session_residency=SessionResidency(max_entries=0),
        ),
    )
    if protection == "turn":
        with coordinator.hold("s"):
            assert sweep(runtime) == ()
    else:
        assert sweep(runtime) == ()
    assert not released


@pytest.mark.asyncio
async def test_usage_stream_holds_session_until_connection_closes() -> None:
    from agent_bridge.routes.runs import register_routes
    from fastapi import FastAPI

    policy = SessionResidency(max_entries=0)
    runtime = cast(BridgeRuntime, SimpleNamespace(session_residency=policy))
    app = FastAPI()
    register_routes(app, runtime)
    route = next(route for route in app.routes if getattr(route, "path", "").endswith("/usage"))

    async def disconnected() -> bool:
        return False

    response = await cast(Any, route).endpoint("s", SimpleNamespace(is_disconnected=disconnected))
    stream = response.body_iterator
    assert await anext(stream) == "data: ready\n\n"

    def reclaim() -> tuple[str, ...]:
        return policy.sweep(
            {"s": object()}, protected=lambda _: False, size=lambda _: 1, release=lambda *_: True
        )

    assert reclaim() == ()
    await stream.aclose()
    assert reclaim() == ("s",)
