import json
from typing import Any
from unittest.mock import Mock

import pytest
from agent.tools.builtin.conversation_access.delivery import SendConversationMessageTool
from agent.tools.registry import ToolRegistry
from llm_providers.responses.request import prepare_request
from llm_providers.spec import ProviderSpec, ResponsesRoute


@pytest.mark.parametrize("responses", [False, True])
def test_delivery_wire_exposes_body_without_attachment_parameter(responses: bool) -> None:
    tools = ToolRegistry([SendConversationMessageTool()]).to_openai_tools()
    if responses:
        spec = ProviderSpec(
            "openai/fixture",
            "fixture",
            context_window=128000,
            responses=ResponsesRoute("fixture", "https://unused.invalid"),
        )
        definition = prepare_request(spec, [], tools, {})["tools"][0]
    else:
        definition = tools[0]["function"]
    assert definition["name"] == "send_conversation_message"
    schema = definition["parameters"]
    assert "attachment_asset_ids" not in json.dumps(definition)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["content"]
    assert set(schema["properties"]) == {
        "content",
        "target_session_id",
        "notify_im",
        "destination_id",
        "source_messages",
    }


@pytest.mark.parametrize("attachment_value", [[], ["a" * 64], None])
def test_old_attachment_parameter_is_rejected_before_submit_and_can_be_removed(
    attachment_value: Any,
) -> None:
    tool = SendConversationMessageTool()
    submit = Mock(return_value={"delivery_id": "delivery", "status": "queued"})
    tool.submit = submit
    registry = ToolRegistry([tool])
    args = {
        "target_session_id": "target",
        "content": "正文\n![示例](https://example.invalid/image.png)\n结尾",
        "notify_im": False,
        "__agent_friend_current_session_id": "source",
        "__agent_friend_run_id": "run",
        "__agent_friend_tool_call_id": "call",
    }
    rejected = registry.invoke(tool.name, {**args, "attachment_asset_ids": attachment_value})
    assert rejected.is_error
    error = json.loads(rejected.text)
    assert error["error"] == "invalid_arguments"
    assert error["rule"] == "additionalProperties"
    assert "删除未知字段" in error["message"]
    submit.assert_not_called()

    corrected = registry.invoke(tool.name, args)
    assert not corrected.is_error
    assert json.loads(corrected.text)["status"] == "queued"
    submit.assert_called_once_with(args)
