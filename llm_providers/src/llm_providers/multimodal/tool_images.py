"""One route-level gate shared by tool execution and native image projection."""

from __future__ import annotations

from ..catalog import model_capabilities
from ..client import LLMClient
from ..protocol import LanguageModel


def supports_tool_images(client: LanguageModel) -> bool:
    if not isinstance(client, LLMClient) or client.tool_images_rejected:
        return False
    spec = client.spec
    route = spec.responses
    capabilities = spec.attachment_capabilities or model_capabilities(
        spec.product_model_id or spec.model
    )
    return (
        capabilities.multimodal_tool_results
        and "image" in capabilities.input_modalities
        and (route is None or route.parameter_profile in {"openai", "deepseek"})
    )


def record_image_rejection(client: LanguageModel, error: Exception) -> bool:
    """Only explicit modality rejections may disable optional image transport."""
    if not isinstance(client, LLMClient) or not supports_tool_images(client):
        return False
    from ..errors import LLMImageInputUnsupportedError

    if not isinstance(error, LLMImageInputUnsupportedError):
        return False
    client.tool_images_rejected = True
    return True
