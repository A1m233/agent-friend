from io import BytesIO
from types import SimpleNamespace

import pytest
from llm_providers.errors import LLMBadRequestError

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
