from copy import deepcopy
from typing import Any

import pytest
from llm_providers.catalog import DEEPSEEK_VISION_CAPABILITIES
from llm_providers.errors import LLMAuthError, LLMBadRequestError, LLMImageInputUnsupportedError
from llm_providers.multimodal.chat_images import project_chat_tool_images
from llm_providers.multimodal.tool_images import record_image_rejection, supports_tool_images

from llm_providers import LLMClient, ProviderSpec


def client(*, chat: bool) -> LLMClient:
    assert chat
    return LLMClient(
        ProviderSpec(
            model="openai/fixture",
            api_key="fixture",
            attachment_capabilities=DEEPSEEK_VISION_CAPABILITIES,
        )
    )


def test_chat_images_follow_complete_tool_batch_and_keep_provenance() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {
            "role": "tool",
            "tool_call_id": "a",
            "content": [
                {"type": "text", "text": "snapshot"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
            ],
        },
        {"role": "tool", "tool_call_id": "b", "content": "done"},
    ]
    original = deepcopy(messages)
    rendered = project_chat_tool_images(messages)
    assert messages == original
    assert [item["role"] for item in rendered] == ["assistant", "tool", "tool", "user"]
    assert rendered[1]["content"] == "snapshot"
    assert "tool_call_id=a" in rendered[-1]["content"][0]["text"]
    assert project_chat_tool_images(rendered) == rendered
    with pytest.raises(LLMBadRequestError, match="transaction_incomplete"):
        project_chat_tool_images(messages[:-1])


def test_explicit_image_rejection_is_bounded_and_does_not_change_other_clients() -> None:
    first, second = client(chat=True), client(chat=True)
    assert supports_tool_images(first)
    assert not record_image_rejection(first, LLMAuthError("images are not supported"))
    assert not record_image_rejection(first, LLMBadRequestError("invalid parameter"))
    error = LLMImageInputUnsupportedError("Model call failed: image_input_unsupported")
    assert record_image_rejection(first, error)
    assert not supports_tool_images(first) and supports_tool_images(second)
    assert not record_image_rejection(first, error)


def test_provider_boundary_classifies_image_rejection_without_leaking_provider_text() -> None:
    import httpx
    import openai
    from llm_providers.error_normalization import map_provider_error

    request = httpx.Request("POST", "https://example.test/responses")
    response = httpx.Response(400, request=request)
    error = openai.BadRequestError(
        "this model does not support image inputs; private fixture",
        response=response,
        body={"error": {"message": "this model does not support image inputs; private fixture"}},
    )
    mapped = map_provider_error(error)
    assert isinstance(mapped, LLMImageInputUnsupportedError)
    assert "private fixture" not in str(mapped)
