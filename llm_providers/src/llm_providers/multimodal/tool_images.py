"""One route-level gate shared by tool execution and native image projection."""

from __future__ import annotations

from ..catalog import model_capabilities
from ..client import LLMClient
from ..protocol import LanguageModel


def supports_tool_images(client: LanguageModel) -> bool:
    if not isinstance(client, LLMClient):
        return False
    spec = client.spec
    route = spec.responses
    if route is None:
        return False
    capabilities = spec.attachment_capabilities or model_capabilities(
        spec.product_model_id or spec.model
    )
    return (
        capabilities.multimodal_tool_results
        and "image" in capabilities.input_modalities
        and route.parameter_profile in {"openai", "deepseek"}
    )
