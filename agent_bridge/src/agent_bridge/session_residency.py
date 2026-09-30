"""Connect idle retention to authoritative scheduler, approval and command state."""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext

from agent.runtime import SessionBusyError

from .assembly import BridgeRuntime

logger = logging.getLogger(__name__)


def sweep(runtime: BridgeRuntime) -> tuple[str, ...]:
    manager = runtime.persistent_session_manager
    agent_runtime = runtime.agent_runtime

    def protected(sid: str) -> bool:
        if manager.has_unresolved_tool_transactions(sid):
            return True
        if agent_runtime is not None and agent_runtime.list_run_snapshots(
            session_ids=(sid,),
            include_recent_terminal=False,
        ):
            return True
        if runtime.approval_broker is not None and runtime.approval_broker.pending_for_session(sid):
            return True
        return runtime.command_runtime is not None and any(
            not process.terminal() for process in runtime.command_runtime.snapshots(session_id=sid)
        )

    def release(sid: str, expected: object) -> bool:
        try:
            with (
                agent_runtime.try_reserve_session_mutation(sid) if agent_runtime else nullcontext(),
                runtime.turn_coordinator.try_hold(sid),
            ):
                if protected(sid):
                    return False
                return manager.release_idle_runtime(sid, expected)
        except SessionBusyError:
            return False

    return runtime.session_residency.sweep(
        manager.resident_states(),
        protected=protected,
        size=manager.resident_size,
        release=release,
        revision=manager.resident_revision,
    )


async def maintain(runtime: BridgeRuntime, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            await asyncio.wait_for(stopping.wait(), timeout=5)
        except TimeoutError:
            try:
                await asyncio.to_thread(sweep, runtime)
            except Exception:
                logger.exception("idle session reclamation failed")
