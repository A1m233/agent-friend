"""Shared session-scoped model mutation service."""

from __future__ import annotations

from contextlib import ExitStack

from agent.runtime import AgentRuntime

from agent import SessionManager
from llm_providers import all_model_ids

from .errors import UnsupportedModelError
from .session_turns import SessionTurnCoordinator

__all__ = ["SessionModelService"]


class SessionModelService:
    def __init__(
        self,
        session_manager: SessionManager,
        turn_coordinator: SessionTurnCoordinator,
        agent_runtime: AgentRuntime | None,
    ) -> None:
        self._sessions = session_manager
        self._turns = turn_coordinator
        self._runtime = agent_runtime

    def switch(self, session_id: str, model: str) -> str:
        if model not in all_model_ids():
            raise UnsupportedModelError(model)
        with ExitStack() as stack:
            if self._runtime is not None:
                stack.enter_context(self._runtime.try_reserve_session_mutation(session_id))
            stack.enter_context(self._turns.try_hold(session_id))
            session = self._sessions.open(session_id)
            conversation = self._sessions.start_conversation(session)
            conversation.switch_model(model)
            return session.current_model
