import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from llm_providers.catalog import KIMI_MULTIMODAL_CAPABILITIES
from llm_providers.errors import LLMBadRequestError
from llm_providers.responses.request import convert_messages

from llm_providers import LLMClient, MultimodalInputAdapter, ProviderSpec


@pytest.mark.parametrize("media", ["application/pdf", "text/plain", "image/png", "application/zip"])
def test_unprojected_user_files_never_reach_provider(media: str) -> None:
    def forbidden(_asset: str) -> BytesIO:
        pytest.fail("user attachment body accessed")

    adapter = MultimodalInputAdapter(open_asset=forbidden)
    message = SimpleNamespace(role="user", attachments=[SimpleNamespace(media_type=media)])
    with pytest.raises(LLMBadRequestError, match="attachment_projection_required"):
        adapter.render_message(
            message, LLMClient(ProviderSpec(model="openai/kimi-k3", api_key="fixture"))
        )


def test_eight_tool_images_reach_wire_but_ninth_is_rejected() -> None:
    data = b"fixture-image-bytes"
    ref = SimpleNamespace(
        kind="image",
        media_type="image/png",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        asset_id="fixture",
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _: BytesIO(data))
    client = LLMClient(
        ProviderSpec(
            model="openai/kimi-k3",
            api_key="fixture",
            attachment_capabilities=KIMI_MULTIMODAL_CAPABILITIES,
        )
    )
    message = SimpleNamespace(
        role="tool",
        content="dialogue pages",
        meta={"tool_call_id": "game"},
        images=[ref] * 8,
    )
    rendered = adapter.render_message(message, client)
    wire = convert_messages([rendered])
    assert wire[0]["type"] == "function_call_output"
    assert sum(part["type"] == "input_image" for part in wire[0]["output"]) == 8
    message.images.append(ref)
    with pytest.raises(LLMBadRequestError, match="image_count_exceeded"):
        adapter.render_message(message, client)
