"""Default failure projection preserves per-run ownership and silent-turn policy."""

from unittest.mock import Mock

from agent.runtime import SystemTriggerEvent, UserEvent
from agent.runtime.models import RunRequest
from agent_bridge.run_errors import persist_run_error

from llm_providers import LLMNetworkError


def test_default_projection_covers_user_visible_system_and_user_runs() -> None:
    for event in (
        UserEvent(session_id="s", user_input="hi"),
        SystemTriggerEvent(
            session_id="s",
            source_kind="scheduled_task",
            system_prompt_addendum="task",
            output_visibility="user",
        ),
    ):
        conv = Mock()
        request = RunRequest(event=event, run_id="r", metadata={"protocol": "fixture"})
        persist_run_error(conv, LLMNetworkError("offline"), request)
        payload = conv.append_run_error_event.call_args.kwargs
        assert payload["run_id"] == "r"
        assert payload["source"] == "fixture"
        assert payload["recoverable"] is True
        assert payload["message"]


def test_default_projection_does_not_surface_memory_only_failures() -> None:
    conv = Mock()
    request = RunRequest(
        event=SystemTriggerEvent(
            session_id="s",
            source_kind="idle_reflection",
            system_prompt_addendum="reflect",
            output_visibility="memory_only",
        )
    )
    persist_run_error(conv, LLMNetworkError("offline"), request)
    conv.append_run_error_event.assert_not_called()
