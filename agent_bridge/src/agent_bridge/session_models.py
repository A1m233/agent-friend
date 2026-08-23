"""Shared session-scoped model mutation service."""

from __future__ import annotations

from contextlib import ExitStack

from agent.runtime import AgentRuntime

from agent import SessionManager
from llm_providers import (
    ModelRuntimeConfig,
    all_model_ids,
    default_model_runtime_config,
    validate_model_runtime_config,
)

from .errors import (
    InvalidRuntimeConfigError,
    UnsupportedModelError,
    UnsupportedReasoningPresetError,
)
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

    def update(
        self,
        session_id: str,
        *,
        model: str | None = None,
        reasoning_preset: str | None = None,
    ) -> ModelRuntimeConfig:
        if model is None and reasoning_preset is None:
            raise InvalidRuntimeConfigError()
        if model is not None and model not in all_model_ids():
            raise UnsupportedModelError(model)
        with ExitStack() as stack:
            if self._runtime is not None:
                stack.enter_context(self._runtime.try_reserve_session_mutation(session_id))
            stack.enter_context(self._turns.try_hold(session_id))
            session = self._sessions.open(session_id)
            current = session.current_model_config
            if model is not None:
                target = (
                    ModelRuntimeConfig(model, reasoning_preset)
                    if reasoning_preset is not None
                    else default_model_runtime_config(model)
                )
            else:
                assert reasoning_preset is not None
                target = ModelRuntimeConfig(current.model_id, reasoning_preset)
            try:
                target = validate_model_runtime_config(target)
            except ValueError as exc:
                raise UnsupportedReasoningPresetError(str(exc)) from exc
            conversation = self._sessions.start_conversation(session)
            conversation.switch_model_config(target)
            return session.current_model_config

    def switch(self, session_id: str, model: str) -> str:
        return self.update(session_id, model=model).model_id
