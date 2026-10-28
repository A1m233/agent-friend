"""Default failure projection preserves per-run ownership and silent-turn policy."""

from unittest.mock import Mock

import httpx
import openai
import pytest
from agent.runtime import SystemTriggerEvent, UserEvent
from agent.runtime.models import RunRequest
from agent_bridge.run_errors import persist_run_error
from llm_providers.error_normalization import map_provider_error

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


@pytest.mark.parametrize("scheduled", [False, True])
def test_model_not_found_projects_actionable_error_for_chat_and_scheduled_runs(
    scheduled: bool,
) -> None:
    body = {
        "error": {
            "type": "invalid_request_error",
            "code": "model_not_found",
            "message": "unknown provider for model private-model",
            "param": "model",
        }
    }
    raw = openai.BadRequestError(
        "private upstream detail",
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://provider.invalid/v1/responses")
        ),
        body=body,
    )
    event = (
        SystemTriggerEvent(
            session_id="s",
            source_kind="scheduled_task",
            system_prompt_addendum="task",
            output_visibility="user",
        )
        if scheduled
        else UserEvent(session_id="s", user_input="hi")
    )
    conv = Mock()
    persist_run_error(
        conv,
        map_provider_error(raw, provider_id="custom/fixture"),
        RunRequest(event=event, run_id="r", metadata={"protocol": "session_request"}),
    )

    conv.append_run_error_event.assert_called_once()
    payload = conv.append_run_error_event.call_args.kwargs
    assert payload["code"] == "model_route_unavailable"
    assert "模型名称与连接配置" in payload["message"]
    assert "服务恢复后重试" in payload["message"]
    assert "private" not in payload["message"]
    assert payload["recoverable"] is False
    assert payload["sanitize_failed_model_call"] is False
    assert payload["run_id"] == "r"
    assert payload["source"] == "session_request"
